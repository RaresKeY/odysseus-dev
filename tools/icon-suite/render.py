#!/usr/bin/env python3
"""Render independent SVG previews and inspect unclipped geometry bounds."""

import concurrent.futures
import io
import json
import subprocess
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[2] / 'docs/icon-suite'


def convert(svg, width, height=None):
    cmd = ['rsvg-convert', '--width', str(width)]
    if height:
        cmd += ['--height', str(height)]
    return subprocess.check_output(cmd, input=svg.encode())


def render_one(icon):
    ident = icon['id']
    svg = (ROOT/icon['svg']).read_text()
    for size in [24, 96]:
        dest = ROOT/'previews'/str(size)/(ident+'.png')
        dest.write_bytes(convert(svg, size))
    padded = svg.replace('viewBox="0 0 24 24"', 'viewBox="-4 -4 32 32"')
    full = Image.open(io.BytesIO(convert(padded, 128, 128))).convert('RGBA')
    alpha = full.getchannel('A')
    bounds = alpha.point(lambda x: 255 if x >= 16 else 0).getbbox()
    assert bounds, ident+' is empty'
    # The padded render makes overflow visible instead of clipping it at export.
    units = [round(x/4-4, 3) for x in bounds]
    overflow = units[0] < 0 or units[1] < 0 or units[2] > 24 or units[3] > 24
    preview = Image.open(ROOT/'previews/96'/(ident+'.png'))
    assert preview.mode == 'RGBA', ident+' has no alpha'
    assert preview.getchannel('A').getextrema()[0] == 0
    return {'id':ident, 'geometry_bounds':units, 'overflow':overflow,
            'preview_bounds_96':list(preview.getchannel('A').getbbox())}


def sheet(name, icons, colors, columns=10):
    cell_w, cell_h = 138, 88
    width = columns*cell_w
    height = 74+((len(icons)+columns-1)//columns)*cell_h
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" color="{colors["fg"]}">',
             f'<rect width="100%" height="100%" fill="{colors["bg"]}"/>',
             f'<g fill="{colors["fg"]}" font-family="monospace"><text x="18" y="28" font-size="17">Odysseus / {name}</text>',
             f'<text x="18" y="49" font-size="11" opacity=".55">{len(icons)} glyphs · 24-unit grid · currentColor · source-backed theme</text></g>']
    for index, icon in enumerate(icons):
        x = (index % columns)*cell_w
        y = 74+(index//columns)*cell_h
        svg = (ROOT/icon['svg']).read_text().replace('width="24" height="24"', f'x="{x+53}" y="{y+7}" width="32" height="32"', 1)
        parts.append(svg)
        parts.append(f'<text x="{x+cell_w/2}" y="{y+63}" fill="{colors["fg"]}" text-anchor="middle" font-size="9" font-family="monospace" opacity=".7">{icon["id"]}</text>')
    parts.append('</svg>')
    text = ''.join(parts)
    dest = ROOT/'sheets'/name
    dest.with_suffix('.svg').write_text(text)
    dest.with_suffix('.png').write_bytes(convert(text, width))


def main():
    catalog = json.loads((ROOT/'catalog.json').read_text())
    themes = json.loads((ROOT/'theme-contract.json').read_text())['themes']
    for size in [24, 96]:
        (ROOT/'previews'/str(size)).mkdir(parents=True, exist_ok=True)
    (ROOT/'sheets').mkdir(exist_ok=True)
    (ROOT/'checks').mkdir(exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(render_one, catalog['icons']))
    failures = [x for x in results if x['overflow']]
    (ROOT/'checks/geometry.json').write_text(json.dumps({'count':len(results), 'overflow':failures, 'results':results}, indent=2)+'\n')
    for category in catalog['categories']:
        sheet(category, [x for x in catalog['icons'] if x['category']==category], themes['dark'], columns=8)
    sheet('all-icons-dark', catalog['icons'], themes['dark'], columns=16)
    # These are full-set ink trials; the browser harness also verifies the real component roles.
    for theme, colors in themes.items():
        sheet('theme-'+theme, catalog['icons'], colors, columns=16)
    print(json.dumps({'rendered':len(results), 'sizes':[24,96], 'overflow_count':len(failures), 'overflow':failures, 'theme_sheets':len(themes)}, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
