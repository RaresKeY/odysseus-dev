#!/usr/bin/env python3
"""Validate asset coverage and the monochrome, portable SVG contract."""
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from PIL import Image

from capture_source import controls
from glyphs import resolve
from comparison import vector_tree, TAGS, ATTRS

ROOT = Path(__file__).resolve().parents[2] / 'docs/icon-suite'
NS = '{http://www.w3.org/2000/svg}'


def main():
    catalog = json.loads((ROOT/'catalog.json').read_text())
    contract = json.loads((ROOT/'theme-contract.json').read_text())
    plan = json.loads((Path(__file__).resolve().parent/'design/icon-plan.json').read_text())
    geometry = json.loads((ROOT/'checks/geometry.json').read_text())
    browser = json.loads((ROOT/'checks/browser.json').read_text())
    ids = {x['id'] for x in catalog['icons']}
    assert len(ids) == len(catalog['icons']) == catalog['count'] == 802
    assert ids == {p.stem for p in (ROOT/'../../static/icons/ui').glob('*.svg') if p.stem != 'sprite'}
    assert ids == {x['id'] for x in plan['icons']}
    assert all(key in ids for key in __import__('build').SYNONYMS)
    bodies = Counter()
    for icon in catalog['icons']:
        ident = icon['id']
        text = (ROOT/icon['svg']).read_text()
        root = ET.fromstring(text)
        assert root.tag == NS+'svg' and root.get('viewBox') == '0 0 24 24', ident
        assert root.get('stroke') == 'currentColor' and root.get('fill') == 'none', ident
        assert root.find(NS+'title').get('id') == root.get('aria-labelledby'), ident
        assert not any(node.tag in {NS+x for x in ['image','script','text','foreignObject','linearGradient','radialGradient']} for node in root.iter()), ident
        for node in root.iter():
            for name,value in node.attrib.items():
                if name in ['fill','stroke','color']:
                    assert value in ['none','currentColor'], (ident,name,value)
                assert not name.endswith('href') and 'url(' not in value, ident
        assert resolve(ident) in text, ident+' does not match its editable source'
        bodies[hashlib.sha256(resolve(ident).encode()).hexdigest()] += 1
        for size in [24,96]:
            image = Image.open(ROOT/'previews'/str(size)/(ident+'.png'))
            assert image.size == (size,size) and image.mode == 'RGBA', ident
            low,high = image.getchannel('A').getextrema()
            assert low == 0 and high > 0, ident
    for family in set(x['reference_board'] for x in catalog['icons']):
        image = Image.open(ROOT/'references'/(family+'.png'))
        assert image.mode == 'RGBA' and image.getchannel('A').getextrema() == (0,255), family
        assert (ROOT/'references'/(family+'.prompt.txt')).stat().st_size > 100, family
    assert len(contract['themes']) == 19
    for rule in contract['css_rules']:
        assert rule['css'] in (ROOT/'evidence/upstream-source'/rule['path']).read_text()
    assert geometry['count'] == len(ids) and not geometry['overflow']
    assert browser['pass'] and not browser['errors'] and not browser['failedRequests']
    assert len(browser['themeRoles']) == 19 and all(x['pass'] for x in browser['themeRoles'])
    fixture = '''<button title="Doesn't repeat">Repeat</button>\n<button title='Use "outline" style'><svg><path/></svg></button>'''
    parsed = controls(fixture,'fixture.html')
    assert parsed[0]['label'] == "Doesn't repeat" and parsed[1]['label'] == 'Use "outline" style'
    assert not parsed[0]['has_svg'] and parsed[1]['has_svg'] and parsed[1]['line'] == 2
    for name,expected in [('control-map.json',1648),('svg-occurrence-map.json',1630)]:
        rows = json.loads((ROOT/'evidence'/name).read_text())
        assert len(rows) == expected and all(x['icon'] is None or x['icon'] in ids for x in rows)
    snapshot = json.loads((ROOT/'evidence/source-snapshot.json').read_text())
    assert snapshot['sha'] == catalog['upstream_sha'] and len(snapshot['files']) == 401
    for path,digest in snapshot['files'].items():
        assert hashlib.sha256((ROOT/'evidence/upstream-source'/path).read_bytes()).hexdigest() == digest, path
    comparison = json.loads((ROOT/'comparison.json').read_text())
    assert comparison['upstream_sha'] == catalog['upstream_sha'] and set(comparison['icons']) == ids
    assert comparison['paired_icons'] == sum(bool(x) for x in comparison['icons'].values())
    raw_hashes = {x['hash'] for x in json.loads((ROOT/'evidence/source-audit.json').read_text())['svg_occurrences']}
    old_svg_count = 0
    def check_tree(tree):
        assert tree['tag'] in TAGS and set(tree['attrs']) <= ATTRS
        assert not any(re.search(r'url\s*\(|javascript:|[<>\"\'{}$;]', value, re.I) for value in tree['attrs'].values())
        for child in tree['children']:
            check_tree(child)
    for key, original in comparison['originals'].items():
        assert original['id'] == key
        if original['kind'] == 'text':
            assert re.fullmatch(r'[A-Za-z0-9 .+_/-]{1,32}', original['text'])
        else:
            old_svg_count += 1
            assert key in raw_hashes
            check_tree(original['tree'])
            old = ET.fromstring((ROOT/original['svg']).read_text())
            assert old.tag == NS+'svg'
            assert not any(node.tag.split('}')[-1] not in TAGS for node in old.iter())
    assert old_svg_count == len(list((ROOT/'originals').glob('*.svg')))
    for variants in comparison['icons'].values():
        assert len({x['original'] for x in variants}) == len(variants)
        for variant in variants:
            assert variant['original'] in comparison['originals'] and variant['line'] > 0
            assert not re.search(r'fixture|prototype|(?:^|/)tests?/', variant['path'], re.I)
            assert variant['ui_role'] in {'action-or-status-icon','disclosure-arrow','formatting-glyph','numeric-stepper','format-or-mode-label','menu-command','text-command'}
    assert any(x['original'] == 'c55d4ca64b7c' and x['ui_role'] == 'disclosure-arrow' for x in comparison['icons']['model'])
    assert any(x['ui_role'] == 'formatting-glyph' for x in comparison['icons']['bold'])
    assert any(x['ui_role'] == 'numeric-stepper' for x in comparison['icons']['transform-width'])
    assert any(x['original'] == '080cb9a85613' for x in comparison['icons']['undo'])
    assert not any(x['original'] == '080cb9a85613' for x in comparison['icons']['check'])
    safe = vector_tree('<svg width="16" height="16" onload="evil()"><path d="M1 1L2 2" onclick="evil()"/></svg>')
    assert safe['attrs']['viewBox'] == '0 0 16 16' and 'onload' not in safe['attrs']
    assert 'onclick' not in safe['children'][0]['attrs']
    for unsafe in ['<svg><script>evil()</script><path d="M1 1"/></svg>', '<svg><path d="${dynamic}"/></svg>', '<svg><path fill="url(https://example.com/external.svg)" d="M1 1"/></svg>']:
        try:
            vector_tree(unsafe)
        except ValueError:
            pass
        else:
            raise AssertionError('Unsupported source SVG was accepted')
    assert (ROOT/'gallery.js').read_text() in (ROOT/'index.html').read_text(), 'Stale gallery output'
    result = {'pass':True,'svg_files':len(ids),'unique_geometry_bodies':len(bodies),
              'intentional_shared_geometry_files':sum(n-1 for n in bodies.values()),
              'png_previews':len(ids)*2,'currentColor_only':True,'external_dependencies':0,
              'reference_sheets':7,'exact_upstream_css_rules':len(contract['css_rules']),
              'themes':19,'source_controls_retained':1648,'source_svg_occurrences_retained':1630,
              'quoted_attribute_regression':'passed','geometry_overflow':0,'pinned_source_files_verified':401,
              'browser_assertions':len(browser['checks']),
              'comparison_pairs':comparison['paired_icons'],'original_svg_files':old_svg_count,
              'original_text_variants':sum(x['kind']=='text' for x in comparison['originals'].values()),
              'comparison_source_label_and_svg_safety_checks':'passed',
              'source_control_roles':sum(len(v) for v in comparison['icons'].values()),
              'limits':['This asset/gallery check does not execute Odysseus. Separate authenticated UI coverage is recorded in integration/visual-coverage.json.']}
    (ROOT/'checks/artifacts.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
