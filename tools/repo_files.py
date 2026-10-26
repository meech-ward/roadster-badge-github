"""One explicit source-release boundary shared by audit and archive tools."""
from pathlib import Path
import stat

from check_app import AppError, PRIVATE_KEY_MARKER, SAFE_COMPONENT, is_secret_name

ROOT = Path(__file__).resolve().parents[1]
ROOT_FILES = {
    'README.md', 'AGENTS.md', 'CONTRIBUTING.md', 'LICENSE',
    'THIRD_PARTY_NOTICES.md', 'requirements.txt', '.gitignore',
    '.gitattributes', '.editorconfig',
}
TREES = ('apps/roadster_sandbox', 'tools', 'docs', 'validation',
         'playground/src', 'playground/compat', 'playground/scripts',
         'playground/e2e', '.github/workflows')
PREVIEW_FILES = {'index.html', 'package.json', 'package-lock.json',
                 'vite.config.js', 'playwright.config.js', 'README.md',
                 'CAPABILITIES.md'}
SKIP = {'__pycache__', '.DS_Store', 'Thumbs.db', 'test-results',
        '.pytest_cache', '.mypy_cache', '.ruff_cache', '.cache', '.venv',
        'node_modules', 'backups', 'dist', 'playwright-report'}
SUFFIXES = {'.py', '.js', '.mjs', '.json', '.md', '.css', '.html', '.sh',
            '.yml', '.yaml', '.txt', '.png'}
WINDOWS_RESERVED = {'CON', 'PRN', 'AUX', 'NUL'} | {
    prefix + str(index) for prefix in ('COM', 'LPT') for index in range(1, 10)}


def validate_source_name(name, seen):
    """Reject names that extract ambiguously on Windows or case-insensitive disks."""
    for index, part in enumerate(name.split('/')):
        explicit_dotfile = name in ROOT_FILES or (index == 0 and part == '.github')
        if ((not explicit_dotfile and not SAFE_COMPONENT.fullmatch(part)) or
                part.endswith('.') or part.split('.')[0].upper() in WINDOWS_RESERVED):
            raise AppError('Unsafe source-release path: ' + name)
    folded = name.casefold()
    if folded in seen and seen[folded] != name:
        raise AppError('Case-insensitive source path collision: ' + name)
    seen[folded] = name


def source_files(root=ROOT):
    """Return sorted (POSIX relative name, bytes), never following symlinks.

    Unlisted top-level trees are never visited: device backups, caches and
    installed dependencies cannot enter a source release accidentally.
    """
    root = Path(root)
    files = []
    seen = {}

    def visit(path):
        if path.name in SKIP or path.suffix in {'.pyc', '.pyo'}:
            return
        name = path.relative_to(root).as_posix()
        validate_source_name(name, seen)
        # Curated roots can start below apps/, playground/ or .github/. Check
        # every ancestor before lstat/read/iterdir, not just the final path.
        parent = root
        for part in path.relative_to(root).parts:
            parent = parent / part
            if parent.is_symlink():
                raise AppError('Source release cannot contain a symlink: ' + name)
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise AppError('Source release cannot contain a symlink: ' + name)
        if is_secret_name(path.name):
            raise AppError('Private filename in source tree: ' + name)
        if stat.S_ISDIR(mode):
            for child in sorted(path.iterdir()):
                visit(child)
            return
        if not stat.S_ISREG(mode):
            raise AppError('Source release requires a regular file: ' + name)
        if path.name != 'LICENSE' and name not in ROOT_FILES and path.suffix not in SUFFIXES:
            raise AppError('Unreviewed file type in source tree: ' + name)
        data = path.read_bytes()
        if PRIVATE_KEY_MARKER.search(data):
            # Test fixtures must assemble fake key markers at runtime as well.
            raise AppError('Private key marker in source tree: ' + name)
        files.append((name, data))

    for name in sorted(ROOT_FILES):
        visit(root / name)
    for name in sorted(PREVIEW_FILES):
        visit(root / 'playground' / name)
    for name in TREES:
        visit(root / name)
    return sorted(files)
