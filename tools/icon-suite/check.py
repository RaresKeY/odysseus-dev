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

ROOT = Path(__file__).resolve().parents[2] / 'docs/icon-suite'
NS = '{http://www.w3.org/2000/svg}'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--full', action='store_true', help='Also verify generated previews, source snapshot and browser receipts')
    args = parser.parse_args()
    if args.full:
        from PIL import Image
    catalog = json.loads((ROOT/'catalog.json').read_text())
    contract = json.loads((ROOT/'theme-contract.json').read_text())
    plan = json.loads((Path(__file__).resolve().parent/'design/icon-plan.json').read_text())
    geometry = json.loads((ROOT/'checks/geometry.json').read_text()) if args.full else None
    browser = json.loads((ROOT/'checks/browser.json').read_text()) if args.full else None
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
        for size in ([24,96] if args.full else []):
            image = Image.open(ROOT/'previews'/str(size)/(ident+'.png'))
            assert image.size == (size,size) and image.mode == 'RGBA', ident
            low,high = image.getchannel('A').getextrema()
            assert low == 0 and high > 0, ident
    for family in set(x['reference_board'] for x in catalog['icons']):
        if args.full:
            image = Image.open(ROOT/'references'/(family+'.png'))
            assert image.mode == 'RGBA' and image.getchannel('A').getextrema() == (0,255), family
        assert (ROOT/'references'/(family+'.prompt.txt')).stat().st_size > 100, family
    assert len(contract['themes']) == 19
    for rule in contract['css_rules']:
        assert rule['css'] in (ROOT/'upstream-components.css').read_text()
        if args.full:
            assert rule['css'] in (ROOT/'evidence/upstream-source'/rule['path']).read_text()
    if args.full:
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
    references = json.loads((ROOT/'references/manifest.json').read_text())
    for board in references['boards']:
        assert hashlib.sha256((ROOT/board['image']).read_bytes()).hexdigest() == board['sha256'], board['id']
    sprite = ET.fromstring((ROOT/'../../static/icons/ui/sprite.svg').read_text())
    assert {x.get('id') for x in sprite.iter(NS+'symbol')} == {'ody-'+x for x in ids}
    embedded = (ROOT/'index.html').read_text()
    assert (ROOT/'gallery.js').read_text() in embedded, 'Inline gallery script is stale'
    for element,value in [('catalog-data',catalog),('contract-data',contract)]:
        match = re.search(r'<script id="'+element+r'" type="application/json">(.*?)</script>',embedded,re.S)
        assert match and json.loads(match.group(1)) == value, element
    result = {'pass':True,'mode':'full' if args.full else 'export','svg_files':len(ids),'unique_geometry_bodies':len(bodies),
              'intentional_shared_geometry_files':sum(n-1 for n in bodies.values()),
              'png_previews':len(ids)*2 if args.full else 0,'currentColor_only':True,'external_dependencies':0,
              'reference_sheets':7,'exact_upstream_css_rules':len(contract['css_rules']),
              'themes':19,'source_controls_retained':1648,'source_svg_occurrences_retained':1630,
              'quoted_attribute_regression':'passed','geometry_overflow':0 if args.full else None,'pinned_source_files_verified':401 if args.full else 0,
              'browser_assertions':len(browser['checks']) if args.full else 0,
              'not_run':['Full authenticated Odysseus integration; this project is a component theme harness.'] + ([] if args.full else ['Generated preview bounds, source copies and browser receipts; use --full after regeneration.'])}
    (ROOT/'checks').mkdir(exist_ok=True)
    (ROOT/'checks/artifacts.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))


if __name__ == '__main__':
    main()
