#!/usr/bin/env python3
"""Run portable host checks without the browser or a connected badge."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pattern', default='test_*.py',
                        help='unittest filename pattern (default: test_*.py)')
    parser.add_argument('--report', type=Path, default=ROOT / 'test-results/host/summary.json',
                        help='JSON result path; relative paths use the current working directory')
    args = parser.parse_args()
    # Some isolated fixtures share test helpers. Both imports work when this
    # command is called from outside the checkout, including paths with spaces.
    sys.path.insert(0, str(ROOT / 'apps/roadster_sandbox'))
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / 'tools/tests'))
    suite = unittest.defaultTestLoader.discover(str(ROOT / 'tools/tests'), pattern=args.pattern)
    start = time.monotonic()
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    files = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
             for path in sorted((ROOT / 'apps/roadster_sandbox').glob('*')) if path.is_file()}
    report = {
        'status': 'passed' if result.wasSuccessful() else 'failed',
        'boundary': 'CPython behavior, geometry and isolated host tooling. Not native rendering, sensor feel or badge performance.',
        'test_pattern': args.pattern,
        'tests_run': result.testsRun,
        'failures': len(result.failures),
        'errors': len(result.errors),
        'skipped': len(result.skipped),
        'skipped_tests': [{'test': str(test), 'reason': reason} for test, reason in result.skipped],
        'seconds': round(time.monotonic() - start, 3),
        'app_sha256': files,
    }
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2) + '\n')
    print('\nHost report:', args.report)
    return 0 if result.wasSuccessful() else 1


if __name__ == '__main__':
    raise SystemExit(main())
