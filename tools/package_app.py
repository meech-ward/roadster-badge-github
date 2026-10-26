#!/usr/bin/env python3
"""Create a deterministic app-only ZIP; never upload or deploy anything."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile
import zipfile

try:
    from .check_app import AppError, WORKSPACE, collect_app
except ImportError:
    from check_app import AppError, WORKSPACE, collect_app


def package_app(slug: str, workspace: Path = WORKSPACE) -> Path:
    bundle = collect_app(slug, workspace)
    output_dir = workspace / "dist"
    output = output_dir / f"{slug}.zip"
    if output_dir.is_symlink() or output.is_symlink():
        raise AppError("The dist directory and output ZIP must not be symlinks.")
    output_dir.mkdir(exist_ok=True)
    temporary: Path | None = None
    try:
        # Stage a complete archive before replacing a previous successful package.
        with tempfile.NamedTemporaryFile(prefix=f".{slug}-", suffix=".zip", dir=output_dir, delete=False) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_STORED) as archive:
            # Stored entries make ZIP bytes independent of host zlib versions.
            for relative, data in bundle.files:
                info = zipfile.ZipInfo(f"{slug}/{relative}", date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                info.compress_type = zipfile.ZIP_STORED
                archive.writestr(info, data)
        temporary.replace(output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("app", help="App folder name under apps/ (roadster_sandbox)")
    args = parser.parse_args()
    try:
        output = package_app(args.app)
    except (OSError, AppError) as error:
        print(f"Cannot package app: {error}", file=sys.stderr)
        return 1
    print(f"Created {output}")
    print("Validated app files and host Python syntax; package only, no deployment or hardware verification.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
