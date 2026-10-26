"""Portable source-release boundaries and an extracted-checkout smoke test."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

from fs_fixtures import make_symlink
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from check_app import AppError
from check_repo import check_repo
from package_repo import package_repo
from repo_files import PREVIEW_FILES, ROOT_FILES, TREES, source_files, validate_source_name

TOOLS = Path(__file__).resolve().parents[1]


class SourceReleaseTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='roadster release ')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.root = self.base / 'source checkout'
        self.root.mkdir()
        for name in ROOT_FILES:
            self.write(name, b'')
        for name in PREVIEW_FILES:
            self.write('playground/' + name, b'')
        for name in TREES:
            self.write(name + '/README.md', b'Fixture source tree.\n')
        self.write('apps/roadster_sandbox/__init__.py', b'import math\nfrom badgeware import *\n')
        self.write('apps/roadster_sandbox/LICENSE', b'Fixture license\n')
        self.write('README.md', b'[Development](docs/DEVELOPMENT.md)\n')
        self.write('docs/DEVELOPMENT.md', b'[Repository root](../)\n')

    def write(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_source_boundary_excludes_generated_and_private_trees(self):
        # None of these trees should be visited, even when their contents
        # would be rejected if placed in an intentional source directory.
        excluded = (
            '.cache/native.wasm', 'backups/secrets.py', 'node_modules/package.json',
            '.venv/credentials.json', 'dist/old-release.zip',
            'playground/public/runtime/micropython.wasm',
            'playground/public/generated/secrets.py',
            'playground/test-results/device.json',
            'tools/node_modules/credentials.json', 'validation/backups/device.json',
            'tools/__pycache__/old.pyc',
        )
        for name in excluded:
            self.write(name, b'not source')
        names = {name for name, _ in source_files(self.root)}
        self.assertFalse(names.intersection(excluded))
        self.assertIn('apps/roadster_sandbox/__init__.py', names)
        self.assertEqual(check_repo(self.root)['status'], 'passed')

    def test_release_is_deterministic_and_checksums_cover_both_archives(self):
        first = package_repo(self.root)
        contents = [path.read_bytes() for path in first]
        entry = self.root / 'apps/roadster_sandbox/__init__.py'
        os.utime(entry, (1000000000, 1000000000))
        entry.chmod(0o755)
        second = package_repo(self.root)
        self.assertEqual(contents, [path.read_bytes() for path in second])
        recorded = dict(line.split('  ', 1)[::-1] for line in second[2].read_text().splitlines())
        for archive in second[:2]:
            self.assertEqual(recorded[archive.name], hashlib.sha256(archive.read_bytes()).hexdigest())
        with zipfile.ZipFile(second[0]) as archive:
            for item in archive.infolist():
                self.assertTrue(item.filename.startswith('roadster-badge/'))
                self.assertEqual(item.date_time, (1980, 1, 1, 0, 0, 0))
                self.assertEqual(item.compress_type, zipfile.ZIP_STORED)
                self.assertEqual(item.external_attr >> 16, 0o100644)
                self.assertNotIn('/dist/', item.filename)

    def test_native_binary_in_curated_source_tree_is_rejected(self):
        self.write('playground/src/micropython.wasm', b'wasm')
        with self.assertRaisesRegex(AppError, 'Unreviewed file type'):
            package_repo(self.root)
        self.assertFalse((self.root / 'dist').exists())

    def test_private_filename_and_key_material_are_rejected(self):
        private = self.write('tools/credentials.json', b'{}')
        with self.assertRaises(AppError):
            source_files(self.root)
        private.unlink()
        # Assemble a fake marker so this test source is itself release-safe.
        marker = b'-----BEGIN ' + b'PRIVATE KEY-----'
        self.write('validation/accidental.txt', marker)
        with self.assertRaisesRegex(AppError, 'Private key marker'):
            source_files(self.root)

    def test_unsafe_cross_platform_names_are_rejected(self):
        for name in ('tools/a\\b.py', 'tools/../escape.py', 'tools/NUL.py',
                     'tools/CON', 'tools/COM1.txt', 'tools/trailing.',
                     'tools/space name.py', 'tools/line\nbreak.py'):
            with self.subTest(name=name), self.assertRaisesRegex(AppError, 'Unsafe'):
                validate_source_name(name, {})

    def test_case_insensitive_path_collision_is_rejected(self):
        seen = {}
        validate_source_name('tools/Example.py', seen)
        with self.assertRaisesRegex(AppError, 'Case-insensitive'):
            validate_source_name('tools/example.py', seen)

    def make_link(self, link, target, directory=False):
        make_symlink(self, link, target, directory=directory)

    def test_source_file_symlink_is_rejected(self):
        external = self.base / 'outside.py'
        external.write_text('outside source\n')
        self.make_link(self.root / 'tools/linked.py', external)
        with self.assertRaisesRegex(AppError, 'symlink'):
            source_files(self.root)

    def test_curated_subtree_cannot_follow_a_symlinked_parent(self):
        external = self.base / 'external playground'
        shutil.move(str(self.root / 'playground'), str(external))
        self.make_link(self.root / 'playground', external, directory=True)
        with self.assertRaisesRegex(AppError, 'symlink'):
            source_files(self.root)

    def test_broken_documentation_link_is_rejected(self):
        self.write('docs/DEVELOPMENT.md', b'[Missing](missing.md)\n')
        with self.assertRaisesRegex(AppError, 'missing source-release link'):
            check_repo(self.root)

    def test_link_to_existing_excluded_artifact_is_still_rejected(self):
        self.write('.cache/result.json', b'{}')
        self.write('docs/DEVELOPMENT.md', b'[Result](../.cache/result.json)\n')
        with self.assertRaisesRegex(AppError, 'missing source-release link'):
            check_repo(self.root)

    def test_documentation_link_cannot_escape_repository(self):
        self.write('README.md', b'[Outside](../outside.md)\n')
        with self.assertRaisesRegex(AppError, 'leaves repository'):
            check_repo(self.root)

    def test_app_cannot_import_an_undeclared_external_dependency(self):
        self.write('apps/roadster_sandbox/__init__.py', b'import requests\n')
        with self.assertRaisesRegex(AppError, 'undeclared app import requests'):
            check_repo(self.root)

    def test_extracted_source_can_audit_package_and_plan_without_original_checkout(self):
        for name in ('check_app.py', 'check_repo.py', 'repo_files.py', 'package_app.py',
                     'package_repo.py', 'install_badge.py', 'badge_transport.py'):
            shutil.copyfile(TOOLS / name, self.root / 'tools' / name)
        source_zip, app_zip, _ = package_repo(self.root)
        expected_app = app_zip.read_bytes()
        extracted = self.base / 'fresh extraction with spaces'
        with zipfile.ZipFile(source_zip) as archive:
            archive.extractall(extracted)
        checkout = extracted / 'roadster-badge'
        shutil.rmtree(self.root)  # No original fixture remains to fall back to.
        env = dict(os.environ)
        env.pop('PYTHONPATH', None)
        for args in (('check_repo.py',), ('package_repo.py',),
                     ('install_badge.py', 'plan', '--package', str(checkout / 'dist/roadster_sandbox.zip'))):
            result = subprocess.run([sys.executable, str(checkout / 'tools' / args[0]), *args[1:]],
                                    cwd=self.base, env=env, text=True, capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse(json.loads(result.stdout)['hardware_accessed'])
        self.assertEqual(expected_app, (checkout / 'dist/roadster_sandbox.zip').read_bytes())


if __name__ == '__main__':
    unittest.main()
