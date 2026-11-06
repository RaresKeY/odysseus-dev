#!/usr/bin/env python3
"""Verify boolean result regions in rendered SVGs, independently of path syntax."""
import io
import json
from pathlib import Path
import subprocess

from PIL import Image

ROOT = Path(__file__).resolve().parents[2]


def main():
    # Interior operand-only and overlap points are clear of all context outlines.
    points = [(6, 12), (12, 12), (18, 12)]
    expected = {'union': [True, True, True], 'subtract': [True, False, False],
                'intersect': [False, True, False], 'exclude': [True, False, True]}
    samples = {}
    for operation, regions in expected.items():
        path = ROOT/'static/icons/ui'/('shape-'+operation+'.svg')
        rendered = subprocess.check_output(['rsvg-convert', '--width', '240', str(path)])
        alpha = Image.open(io.BytesIO(rendered)).getchannel('A')
        values = [alpha.getpixel((x*10, y*10)) for x, y in points]
        assert [v >= 40 for v in values] == regions, (operation, values, regions)
        samples[operation] = values
    result = {'pass': True, 'sample_points': points, 'alpha_by_operation': samples,
              'meaning': 'left-only, intersection and right-only regions match each boolean operation'}
    (ROOT/'docs/icon-suite/integration/visual-fixes/geometry-contract.json').write_text(json.dumps(result, indent=2)+'\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
