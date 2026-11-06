#!/usr/bin/env python3
"""Validate asset coverage and the monochrome, portable SVG contract."""
import argparse
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from capture_source import controls
from glyphs import resolve
from comparison import vector_tree, TAGS, ATTRS

ROOT = Path(__file__).resolve().parents[2] / 'docs/icon-suite'
NS = '{http://www.w3.org/2000/svg}'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument('--full', action='store_true',
                        help='also verify generated previews, source copies and browser receipts')
    parser.add_argument('--output', type=Path, help='save the validation receipt as JSON')
    args = parser.parse_args(argv)
    # The portable check needs only committed files and the standard library.
    # Rendered artifacts and Pillow belong to the opt-in authoring check.
    if args.full:
        from PIL import Image
        geometry = json.loads((ROOT/'checks/geometry.json').read_text())
        browser = json.loads((ROOT/'checks/browser.json').read_text())
    catalog = json.loads((ROOT/'catalog.json').read_text())
    contract = json.loads((ROOT/'theme-contract.json').read_text())
    plan = json.loads((Path(__file__).resolve().parent/'design/icon-plan.json').read_text())
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
        if args.full:
            for size in [24,96]:
                with Image.open(ROOT/'previews'/str(size)/(ident+'.png')) as image:
                    assert image.size == (size,size) and image.mode == 'RGBA', ident
                    low,high = image.getchannel('A').getextrema()
                    assert low == 0 and high > 0, ident
    sprite = ET.parse(ROOT/'../../static/icons/ui/sprite.svg').getroot()
    symbols = list(sprite.iter(NS+'symbol'))
    assert len(symbols) == len(ids)
    assert {node.get('id') for node in symbols} == {'ody-'+ident for ident in ids}
    for node in symbols:
        assert node.get('viewBox') == '0 0 24 24'
        exported = ET.parse(ROOT/'../../static/icons/ui'/(node.get('id')[4:]+'.svg')).getroot()
        # The sprite omits the standalone accessible title, but uses the same geometry.
        assert [ET.tostring(child) for child in node] == [
            ET.tostring(child) for child in exported if child.tag != NS+'title']
    references = json.loads((ROOT/'references/manifest.json').read_text())['boards']
    families = {x['reference_board'] for x in catalog['icons']}
    assert {board['id'] for board in references} == families
    for board in references:
        assert hashlib.sha256((ROOT/board['image']).read_bytes()).hexdigest() == board['sha256'], board['id']
        assert (ROOT/board['prompt']).stat().st_size > 100, board['id']
        if args.full:
            with Image.open(ROOT/board['image']) as image:
                assert image.mode == 'RGBA' and image.getchannel('A').getextrema() == (0,255), board['id']
    assert len(contract['themes']) == 19
    if args.full:
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
    if args.full:
        for path,metadata in snapshot['files'].items():
            assert hashlib.sha256((ROOT/'evidence/upstream-source'/path).read_bytes()).hexdigest() == metadata['sha256'], path
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
    result = {'pass':True,'mode':'full' if args.full else 'portable','svg_files':len(ids),'unique_geometry_bodies':len(bodies),
              'intentional_shared_geometry_files':sum(n-1 for n in bodies.values()),
              'png_previews':len(ids)*2 if args.full else 0,'currentColor_only':True,'external_dependencies':0,
              'reference_sheets':7,'exact_upstream_css_rules':len(contract['css_rules']) if args.full else 0,
              'themes':19,'source_controls_retained':1648,'source_svg_occurrences_retained':1630,
              'quoted_attribute_regression':'passed','geometry_overflow':0 if args.full else None,'pinned_source_files_verified':401 if args.full else 0,
              'browser_assertions':len(browser['checks']) if args.full else 0,
              'comparison_pairs':comparison['paired_icons'],'original_svg_files':old_svg_count,
              'original_text_variants':sum(x['kind']=='text' for x in comparison['originals'].values()),
              'comparison_source_label_and_svg_safety_checks':'passed',
              'source_control_roles':sum(len(v) for v in comparison['icons'].values()),
              'not_run':[] if args.full else ['Rendered previews and overflow','Pinned source copies and CSS rules','Browser assertions and theme trials'],
              'limits':['This asset/gallery check does not execute Odysseus. Separate authenticated UI coverage is recorded in integration/visual-coverage.json.']}
    if args.output:
        args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
