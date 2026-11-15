#!/usr/bin/env python3
"""List every suite file without importing or executing tests.

This is a source inventory, not expanded pytest collection or a pass report.
Use --nodes to include Python definition names; parametrized/dynamic cases
remain owned by pytest collection in the qualified test environment.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
import hashlib
import json
from pathlib import Path


def inventory(root, *, nodes=False):
    root = Path(root)
    files = []
    for path in sorted((root / 'tests').rglob('*')):
        if any(part in {'__pycache__', '.pytest_cache', 'node_modules'} for part in path.relative_to(root).parts):
            continue
        if not path.is_file() or path.is_symlink():
            continue
        python = path.name.startswith('test_') and path.suffix == '.py'
        javascript = path.suffix in {'.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx'}
        raw = path.read_bytes()
        row = {'path': path.relative_to(root).as_posix(),
               'kind': 'pytest' if python else ('javascript-source' if javascript else 'fixture-or-support'),
               'sha256': hashlib.sha256(raw).hexdigest()}
        if python:
            tree = ast.parse(raw, filename=str(path))
            definitions = []
            def visit(body, prefix=''):
                for item in body:
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name.startswith('test_'):
                        definitions.append(prefix + item.name)
                    elif isinstance(item, ast.ClassDef):
                        visit(item.body, prefix + item.name + '::')
            visit(tree.body)
            row['definitions'] = len(definitions)
            if nodes:
                row['nodes'] = [row['path'] + '::' + name for name in definitions]
        files.append(row)
    return {'schema': 'odysseus-fork-test-inventory/v1',
            'scope': 'Every file under tests except symlinks and generated caches. Pytest files, JavaScript scenario/support sources and remaining fixture/support files are distinguished; definitions are not expanded parametrizations.',
            'counts': dict(Counter(f['kind'] for f in files)),
            'python_definitions': sum(f.get('definitions', 0) for f in files), 'files': files}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--nodes', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    result = inventory(args.root, nodes=args.nodes)
    args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps({k: v for k, v in result.items() if k != 'files'}))


if __name__ == '__main__':
    main()
