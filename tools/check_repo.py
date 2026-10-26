#!/usr/bin/env python3
"""Audit shareable source, app imports and local documentation links offline."""
import ast
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlparse

from check_app import AppError, collect_app
from repo_files import ROOT, source_files


def check_repo(root=ROOT):
    root = Path(root).resolve()
    files = source_files(root)
    names = {name for name, _ in files}
    errors = []
    bundle = collect_app('roadster_sandbox', root)
    local_modules = {Path(name).stem for name, _ in bundle.files if name.endswith('.py')}
    allowed_imports = local_modules | {'array', 'gc', 'math', 'badgeware', 'pico3d'}
    for name, data in files:
        if Path(name).suffix == '.png':
            continue
        source = data.decode('utf-8')
        if re.search(r'/(?:Users|home)/[A-Za-z0-9_.-]+/', source):
            errors.append(name + ': contains a personal absolute path')
        if name.startswith('apps/') and name.endswith('.py'):
            for node in ast.walk(ast.parse(source)):
                imports = []
                if isinstance(node, ast.Import):
                    imports = [item.name.split('.')[0] for item in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imports = [node.module.split('.')[0]]
                for module in imports:
                    if module not in allowed_imports:
                        errors.append(name + ': undeclared app import ' + module)
        if name.endswith('.md'):
            for target in re.findall(r'!?\[[^\]\n]*\]\(([^\s)]+)(?:\s+"[^"]*")?\)', source):
                parsed = urlparse(target.strip('<>'))
                if parsed.scheme or parsed.netloc or not parsed.path:
                    continue
                resolved = ((root / name).parent / unquote(parsed.path)).resolve()
                try:
                    relative = resolved.relative_to(root).as_posix()
                except ValueError:
                    errors.append(name + ': documentation link leaves repository: ' + target)
                    continue
                if (relative != '.' and relative not in names and
                        not any(n.startswith(relative + '/') for n in names)):
                    errors.append(name + ': missing source-release link: ' + target)
    if errors:
        raise AppError('\n'.join(errors))
    return {'status': 'passed', 'source_files': len(files),
            'source_bytes': sum(len(data) for _, data in files),
            'app_files': len(bundle.files),
            'boundary': 'Curated source boundary, local Markdown links, app syntax and declared imports; no hardware access.'}


if __name__ == '__main__':
    try:
        print(json.dumps(check_repo(), indent=2))
    except (OSError, UnicodeError, AppError) as error:
        print('Repository audit failed: ' + str(error), file=sys.stderr)
        raise SystemExit(1)
