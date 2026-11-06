#!/usr/bin/env python3
"""Verify the maintainer's icon choices and retained control affordances."""
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
DOCS = ROOT/'docs/icon-suite/integration'


def main():
    selection = json.loads((DOCS/'choices.json').read_text())
    manifest = json.loads((DOCS/'application-manifest.json').read_text())
    expected = {x['id'] for x in manifest['entries'] if x['status'] == 'applied'}
    selected = {x['id'] for x in selection['choices'] if x['winner'] == 'new'}
    assert selected == {x['id'] for x in manifest['entries']}
    assert manifest['default'] == 'old' and manifest['unspecified_keep_old'] == 544
    source_files = list((ROOT/'static').rglob('*.js')) + list((ROOT/'frontend/src/features/notes').rglob('*.js')) + [ROOT/'static/index.html', ROOT/'static/login.html']
    actual = set(re.findall(r'data-ui-icon="([\w-]+)"', '\n'.join(p.read_text() for p in source_files)))
    assert actual == expected and not actual-selected, (actual-expected,expected-actual)
    doc = (ROOT/'static/js/document.js').read_text()
    for key in ['heading', 'font']:
        button = re.search(r'<button\b[^>]*data-dd="'+key+r'"[^>]*>.*?</button>',doc,re.S).group()
        assert button.count('<svg') == 2 and 'width="8"' in button
    index = (ROOT/'static/index.html').read_text()
    reasoning = re.search(r'<button\b[^>]*id="reasoning-effort-btn"[^>]*>.*?</button>',index,re.S).group()
    assert 'data-ui-icon="reasoning"' in reasoning and 'reasoning-effort-chevron' in reasoning
    model = re.search(r'<button\b[^>]*id="model-picker-btn"[^>]*>.*?</button>',index,re.S).group()
    assert 'data-ui-icon' not in model and 'points="6 15 12 9 18 15"' in model
    transform = (ROOT/'static/js/editor/build/transform-popup.js').read_text()
    signs = re.findall(r'<button\b[^>]*data-spin="(up|down)"[^>]*>(.*?)</button>',transform,re.S)
    assert signs and all(text == ('+' if direction=='up' else '−') for direction,text in signs)
    assert 'data-ui-icon="transform-width"' in transform and 'data-ui-icon="transform-height"' in transform
    export = (ROOT/'static/js/editor/export-dialog.js').read_text()
    for name in ['PNG','JPEG']:
        assert '<span class="ody-ui-label-text">'+name+'</span>' in export
    assert 'grid-template-columns: repeat(4, minmax(0, 1fr))' in (ROOT/'static/css/13-image-editor.css').read_text()
    assert 'sidebar-menu-icon' in index and 'data-ui-icon="sidebar-close"' in index
    assert '#hamburger-btn[aria-expanded="true"] > .ody-ui-icon' in (ROOT/'static/css/00-tokens.css').read_text()
    assert "sidebarHidden ? 'Show sidebar' : 'Hide sidebar'" in (ROOT/'static/js/sidebar-layout.js').read_text()
    assert 'odysseus-v1139-pr40-editor-precache' in (ROOT/'static/sw.js').read_text()
    for name in ['cardRendering.js','drawing.js','panel.js']:
        assert (ROOT/'frontend/src/features/notes/ui'/name).read_bytes() == (ROOT/'static/js/features/notes/ui'/name).read_bytes()
    assert 'note-form-draw-text-badge' in (ROOT/'static/js/features/notes/ui/drawing.js').read_text()
    print(json.dumps({'pass':True,'selected_new':len(selected),'applied':len(expected),'default':'old','retained_for_review':sorted(selected-expected),
      'contracts':['selected-only changes','model picker retained','semantic icons plus disclosure carets','numeric signs retained','format labels retained','four format columns','service-worker cache version']},indent=2))


if __name__ == '__main__':main()
