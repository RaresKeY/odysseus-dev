"""A synthetic credential exemption must not exempt other secrets or files."""
from pathlib import Path
import re
import tomllib

import pytest


CONFIG = tomllib.loads((Path(__file__).resolve().parents[1] / '.gitleaks.toml').read_text())


def test_default_rules_remain_enabled():
    assert CONFIG['extend']['useDefault'] is True
    assert not CONFIG['extend'].get('disabledRules')


@pytest.mark.parametrize('path,value,expected', [
    ('tests/test_model_auto.py', 'CHANGED_SYNTHETIC', True),
    ('project/tests/test_model_auto.py', 'CHANGED_SYNTHETIC', True),
    ('tests/test_model_auto.py.bak', 'CHANGED_SYNTHETIC', False),
    ('src/test_model_auto.py', 'CHANGED_SYNTHETIC', False),
    ('tests/test_other.py', 'CHANGED_SYNTHETIC', False),
    ('tests/test_model_auto.py', 'UNREVIEWED_SYNTHETIC', False),
    ('tests/test_model_auto.py', 'XCHANGED_SYNTHETIC', False),
])
def test_model_routing_exception_requires_exact_value_and_file(path, value, expected):
    (rule,) = [entry for entry in CONFIG['allowlists']
               if entry.get('description') == 'Exact nonfunctional model-routing credential fixture']
    assert rule['targetRules'] == ['generic-api-key']
    assert rule['condition'] == 'AND'
    assert rule['regexTarget'] == 'line'
    assert set(rule) == {'description', 'targetRules', 'condition', 'paths', 'regexTarget', 'regexes'}
    statement = "if mutation=='credential':" + ".".join(("row", "api_key")) + "=" + repr(value)
    matches = (any(re.search(pattern, path) for pattern in rule['paths'])
               and any(re.search(pattern, statement) for pattern in rule['regexes']))
    assert matches is expected
