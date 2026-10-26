#!/usr/bin/env python3
"""Validate local badge app files without executing them or importing badgeware.

Python checks use the host CPython parser/compiler, not MicroPython. Hardware
execution and MicroPython library/API compatibility need separate validation.
"""

from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path
import re
import stat
import sys


WORKSPACE = Path(__file__).resolve().parents[1]
SLUG = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z")
SAFE_COMPONENT = re.compile(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]*\Z")
HOST_JUNK = {".DS_Store", "Thumbs.db", "__MACOSX", "__pycache__", ".git", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
SECRET_STEMS = {"secrets", "secret", "credentials", "credential", "id_rsa", "id_dsa", "id_ecdsa", "id_ed25519"}
PRIVATE_KEY_MARKER = re.compile(rb"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----")


class AppError(ValueError):
    """A file tree is unsuitable for an app-only archive."""


@dataclass(frozen=True)
class AppFiles:
    slug: str
    root: Path
    files: tuple[tuple[str, bytes], ...]
    skipped: tuple[str, ...]

    @property
    def size(self) -> int:
        return sum(len(data) for _, data in self.files)


def is_secret_name(name: str) -> bool:
    lower = name.lower()
    stem = lower.split(".", 1)[0]
    return (
        lower == ".env"
        or lower.startswith(".env.")
        or stem in SECRET_STEMS
        or lower.endswith((".key", ".p12", ".pfx"))
    )


def collect_app(slug: str, workspace: Path = WORKSPACE) -> AppFiles:
    """Read and validate apps/<slug>; keep bytes unchanged for packaging."""
    if not SLUG.fullmatch(slug):
        raise AppError("Use an app name of 1–64 lowercase letters, digits, _ or -, starting with a letter.")
    apps = workspace / "apps"
    root = apps / slug
    if apps.is_symlink() or root.is_symlink():
        raise AppError("The apps directory and app folder must not be symlinks.")
    if not root.is_dir():
        raise AppError(f"App folder does not exist: {root}")

    files: list[tuple[str, bytes]] = []
    skipped: list[str] = []

    def visit(folder: Path) -> None:
        for path in sorted(folder.iterdir(), key=lambda item: item.name):
            relative = path.relative_to(root).as_posix()
            mode = path.lstat().st_mode
            # Check links even when their name looks like a file we would omit.
            if stat.S_ISLNK(mode):
                raise AppError(f"{relative}: symlinks are not allowed in an app.")
            if is_secret_name(path.name):
                raise AppError(f"{relative}: secret or private-key files must stay outside the app folder.")
            if path.name in HOST_JUNK or path.name.startswith("._") or path.suffix.lower() in {".pyc", ".pyo"}:
                skipped.append(relative)
                continue
            if not SAFE_COMPONENT.fullmatch(path.name):
                raise AppError(f"{relative}: use a relative path containing letters, digits, _, - and dots; no hidden files.")
            if stat.S_ISDIR(mode):
                visit(path)
                continue
            if not stat.S_ISREG(mode):
                raise AppError(f"{relative}: only regular files and folders can be packaged.")
            data = path.read_bytes()
            if PRIVATE_KEY_MARKER.search(data):
                raise AppError(f"{relative}: contains a private key; move it outside the app folder.")
            if path.suffix.lower() == ".py":
                try:
                    source = data.decode("utf-8-sig")
                    tree = ast.parse(source, filename=relative)
                    compile(tree, relative, "exec")
                except (UnicodeDecodeError, SyntaxError, ValueError) as error:
                    raise AppError(f"{relative}: host Python syntax/UTF-8 check failed: {error}") from error
            files.append((relative, data))

    visit(root)
    if not any(name == "__init__.py" for name, _ in files):
        raise AppError(f"{root}: add an __init__.py entry file at the app folder root.")
    files.sort(key=lambda item: item[0])
    return AppFiles(slug, root, tuple(files), tuple(skipped))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", help="App folder name under apps/ (roadster_sandbox)")
    args = parser.parse_args()
    try:
        bundle = collect_app(args.app)
    except (OSError, AppError) as error:
        print(f"Cannot validate app: {error}", file=sys.stderr)
        return 1
    print(f"Checked {bundle.root}: {len(bundle.files)} files, {bundle.size} bytes; host Python syntax passed.")
    if bundle.skipped:
        print(f"Omitted {len(bundle.skipped)} host junk entries.")
    print("This does not prove MicroPython/API compatibility or hardware behavior.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
