#!/usr/bin/env python3
"""Build deterministic, self-contained source and app ZIPs without deployment."""
import hashlib
from pathlib import Path
import sys
import tempfile
import zipfile

from check_app import AppError
from check_repo import check_repo
from package_app import package_app
from repo_files import ROOT, source_files


def package_repo(root=ROOT):
    root = Path(root)
    check_repo(root)
    app = package_app('roadster_sandbox', root)
    output = root / 'dist/roadster-badge-source.zip'
    checksums = root / 'dist/SHA256SUMS'
    if output.is_symlink() or checksums.is_symlink():
        raise AppError('Release outputs must not be symlinks')
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=output.parent, suffix='.zip', delete=False) as handle:
            temporary = Path(handle.name)
        with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED) as archive:
            for name, data in source_files(root):
                info = zipfile.ZipInfo('roadster-badge/' + name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
        temporary.replace(output)
        temporary = None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
    sums = [(p.name, hashlib.sha256(p.read_bytes()).hexdigest()) for p in (app, output)]
    checksums.write_text(''.join(value + '  ' + name + '\n' for name, value in sums), encoding='utf-8')
    return output, app, checksums


if __name__ == '__main__':
    try:
        for result in package_repo():
            print(result)
    except (OSError, UnicodeError, AppError) as error:
        print('Cannot package source: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
