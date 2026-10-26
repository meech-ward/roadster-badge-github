"""Archive contract and failure cases, using isolated temporary workspaces."""

from pathlib import Path
import os
import tempfile
import unittest
import zipfile

from tools.check_app import AppError, collect_app
from tools.package_app import package_app
from fs_fixtures import make_symlink


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name)
        self.app = self.workspace / "apps" / "demo"
        self.app.mkdir(parents=True)
        self.write("__init__.py", b"from badgeware import *\r\nimport helpers\r\n")
        self.write("helpers.py", b"VALUE = 42\n")

    def write(self, name, data):
        path = self.app / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def test_reproducible_bytes_and_app_only_paths(self):
        binary = bytes(range(256)) * 5000  # No inherited badge.select 1 MiB cap.
        self.write("assets/pixels.bin", binary)
        (self.workspace / "AGENTS.md").write_text("Computer-only instructions")
        output = package_app("demo", self.workspace)
        first = output.read_bytes()
        for path in self.app.rglob("*"):
            os.utime(path, (1750000000, 1750000000))
            path.chmod(0o755 if path.is_dir() else 0o600)
        package_app("demo", self.workspace)
        self.assertEqual(first, output.read_bytes())
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.namelist(), ["demo/__init__.py", "demo/assets/pixels.bin", "demo/helpers.py"])
            self.assertEqual(archive.read("demo/assets/pixels.bin"), binary)
            self.assertEqual(archive.read("demo/__init__.py"), (self.app / "__init__.py").read_bytes())
            self.assertTrue(all(item.date_time == (1980, 1, 1, 0, 0, 0) for item in archive.infolist()))

    def test_checks_sibling_syntax_and_preserves_previous_package_on_failure(self):
        output = package_app("demo", self.workspace)
        previous = output.read_bytes()
        self.write("helpers.py", b"def broken(:\n")
        with self.assertRaisesRegex(AppError, "helpers.py.*syntax"):
            package_app("demo", self.workspace)
        self.assertEqual(previous, output.read_bytes())

    def test_compile_rejects_invalid_ast_context(self):
        self.write("helpers.py", b"return 3\n")
        with self.assertRaisesRegex(AppError, "outside function"):
            collect_app("demo", self.workspace)

    def test_requires_root_entry_and_utf8(self):
        (self.app / "__init__.py").unlink()
        with self.assertRaisesRegex(AppError, "entry file"):
            collect_app("demo", self.workspace)
        self.write("__init__.py", b"# \xff\n")
        with self.assertRaisesRegex(AppError, "UTF-8"):
            collect_app("demo", self.workspace)

    def test_omits_host_junk(self):
        for name in (".DS_Store", "._assets", "helpers.pyc", "__pycache__/helpers.pyc", ".git/config"):
            self.write(name, b"junk")
        bundle = collect_app("demo", self.workspace)
        self.assertEqual([name for name, _ in bundle.files], ["__init__.py", "helpers.py"])
        self.assertEqual(len(bundle.skipped), 5)

    def test_rejects_named_secrets_and_private_key_contents(self):
        for name in ("secrets.py", ".env", ".env.local", "credentials.json", "assets/device.key"):
            with self.subTest(name=name):
                secret = self.write(name, b"do-not-package")
                with self.assertRaisesRegex(AppError, "secret or private-key"):
                    collect_app("demo", self.workspace)
                secret.unlink()
        self.write("assets/data.txt", b"-----BEGIN OPENSSH " + b"PRIVATE KEY-----\n...")
        with self.assertRaisesRegex(AppError, "contains a private key"):
            collect_app("demo", self.workspace)

    def test_rejects_symlinks_even_when_named_as_host_junk(self):
        outside = self.workspace / "outside.txt"
        outside.write_bytes(b"outside")
        for name in ("link.bin", ".DS_Store"):
            with self.subTest(name=name):
                link = self.app / name
                make_symlink(self, link, outside)
                with self.assertRaisesRegex(AppError, "symlinks"):
                    collect_app("demo", self.workspace)
                link.unlink()
        make_symlink(self, self.app / "nested", self.workspace, directory=True)
        with self.assertRaisesRegex(AppError, "symlinks"):
            collect_app("demo", self.workspace)

    def test_rejects_root_links_and_path_traversal(self):
        for name in ("../demo", "/tmp/demo", "demo/../demo", "Demo", "a\\b"):
            with self.subTest(name=name), self.assertRaises(AppError):
                collect_app(name, self.workspace)
        link = self.workspace / "apps" / "linked"
        make_symlink(self, link, self.app, directory=True)
        with self.assertRaisesRegex(AppError, "symlinks"):
            collect_app("linked", self.workspace)

    def test_rejects_output_links(self):
        outside = self.workspace / "outside"
        outside.mkdir()
        dist = self.workspace / "dist"
        make_symlink(self, dist, outside, directory=True)
        with self.assertRaisesRegex(AppError, "symlinks"):
            package_app("demo", self.workspace)
        dist.unlink()
        dist.mkdir()
        make_symlink(self, dist / "demo.zip", outside / "package.zip")
        with self.assertRaisesRegex(AppError, "symlinks"):
            package_app("demo", self.workspace)


if __name__ == "__main__":
    unittest.main()
