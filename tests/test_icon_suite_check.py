"""The portable icon audit must work from committed files without optional tools."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run_check(root, *args):
    return subprocess.run([sys.executable, '-S', str(root/'tools/icon-suite/check.py'), *args],
                          cwd=root, text=True, capture_output=True, check=False)


def test_portable_check_needs_only_standard_library():
    result = run_check(ROOT)
    assert result.returncode == 0, result.stderr
    receipt = json.loads(result.stdout)
    assert receipt['pass'] and receipt['mode'] == 'portable'
    assert receipt['svg_files'] == 802
    assert receipt['png_previews'] == receipt['browser_assertions'] == 0
    assert receipt['pinned_source_files_verified'] == 0
    assert receipt['geometry_overflow'] is None
    assert receipt['not_run']


@pytest.fixture
def portable_tree(tmp_path):
    # Leave generated previews, machine receipts and captured source copies absent.
    for directory in ['tools/icon-suite', 'static/icons/ui']:
        shutil.copytree(ROOT/directory, tmp_path/directory)
    docs = tmp_path/'docs/icon-suite'
    docs.mkdir(parents=True)
    for name in ['catalog.json', 'theme-contract.json', 'comparison.json', 'gallery.js', 'index.html']:
        shutil.copy2(ROOT/'docs/icon-suite'/name, docs/name)
    for directory in ['originals', 'references']:
        shutil.copytree(ROOT/'docs/icon-suite'/directory, docs/directory)
    evidence = docs/'evidence'
    evidence.mkdir()
    for name in ['control-map.json', 'svg-occurrence-map.json', 'source-snapshot.json', 'source-audit.json']:
        shutil.copy2(ROOT/'docs/icon-suite/evidence'/name, evidence/name)
    return tmp_path


def test_clean_tree_audit_and_explicit_receipt(portable_tree):
    output = portable_tree/'receipt.json'
    result = run_check(portable_tree, '--output', str(output))
    assert result.returncode == 0, result.stderr
    assert json.loads(output.read_text()) == json.loads(result.stdout)
    assert not (portable_tree/'docs/icon-suite/checks').exists()


@pytest.mark.parametrize('asset', ['static/icons/ui/sprite.svg',
                                  'docs/icon-suite/references/navigation.png'])
def test_portable_audit_rejects_corrupted_exports(portable_tree, asset):
    path = portable_tree/asset
    if path.suffix == '.svg':
        path.write_text(path.read_text().replace('id="ody-sailboat"', 'id="ody-missing"', 1))
    else:
        path.write_bytes(path.read_bytes()+b'corrupt')
    result = run_check(portable_tree)
    assert result.returncode != 0
    assert 'AssertionError' in result.stderr


def test_full_check_does_not_report_success_without_optional_inputs(portable_tree):
    result = run_check(portable_tree, '--full')
    assert result.returncode != 0
    assert not result.stdout


def test_unknown_options_are_rejected():
    result = run_check(ROOT, '--ful')
    assert result.returncode != 0
    assert 'unrecognized arguments' in result.stderr
