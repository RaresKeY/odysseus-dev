#!/usr/bin/env python3
"""Read an explicit Git snapshot without checking it out or executing its code."""

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path


def controls(text, path):
    result = []
    for match in re.finditer(r'<button\b([^>]*)>(.*?)</button>', text, re.S | re.I):
        attrs, body = match.group(1), match.group(2)

        def attr(name):
            found = re.search(r'\b'+re.escape(name)+r'\s*=\s*(["\'])(.*?)\1', attrs, re.S)
            return found.group(2) if found else ''

        label = attr('aria-label') or attr('title') or re.sub('<[^>]*>', ' ', body)
        result.append({'path':path, 'line':text.count('\n', 0, match.start())+1, 'id':attr('id'),
                       'label':re.sub(r'\s+', ' ', label).strip()[:240], 'has_svg':'<svg' in body,
                       'dynamic_icon':bool(re.search(r'icon|Icon|SVG|svg', body))})
    return result


def capture(repo, ref, out):
    def git(*args):
        return subprocess.check_output(['git', '-C', str(repo), *args])

    sha = git('rev-parse', ref).decode().strip()
    paths = git('ls-tree', '-r', '--name-only', sha).decode().splitlines()
    selected = [p for p in paths if p.startswith(('static/', 'frontend/', 'src/', 'routes/', 'resources/', 'assets/'))
                and Path(p).suffix in ('.js', '.mjs', '.html', '.css', '.svg', '.py', '.json')
                and not any(part in p for part in ['/lib/', '/fonts/', '/vendor/', '/node_modules/', '/backgrounds/'])]
    out.mkdir(parents=True, exist_ok=True)
    occurrences, icon_lines, color_lines, all_controls = [], [], [], []
    process = subprocess.Popen(['git', '-C', str(repo), 'cat-file', '--batch'],
                               stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        for path in selected:
            process.stdin.write((sha+':'+path+'\n').encode())
            process.stdin.flush()
            header = process.stdout.readline().decode().strip().split()
            if len(header) != 3 or header[-1] == 'missing':
                raise RuntimeError('Missing source blob: '+path)
            raw = process.stdout.read(int(header[-1]))
            process.stdout.read(1)
            try:
                source = raw.decode('utf-8')
            except UnicodeDecodeError:
                continue
            is_ui = path.startswith(('static/js/', 'static/css/', 'frontend/src/')) or path.endswith(('.html','.svg'))
            if is_ui:
                target = out/'upstream-source'/path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(source)
            if path.endswith(('.js','.mjs','.html','.svg')):
                for match in re.finditer(r'<svg\b[^>]*>.*?</svg>', source, re.S | re.I):
                    shape = re.sub(r'\s+', ' ', match.group())
                    occurrences.append({'path':path, 'line':source.count('\n', 0, match.start())+1,
                                        'hash':hashlib.sha256(shape.encode()).hexdigest()[:12], 'svg':shape,
                                        'context':source[max(0,match.start()-180):min(len(source),match.end()+140)]})
            if path.endswith(('.js','.html')):
                all_controls.extend(controls(source,path))
            if path.endswith(('.js','.html','.css')):
                for line_number, line in enumerate(source.splitlines(), 1):
                    if re.search(r'data-icon|icon:\s*|icon\s*=|Icon\s*:|iconHtml|iconSvg|ICONS|getIcon|createIcon|lucide|feather|material-icons|fa-[a-z]',line) or any(0x1F300<=ord(c)<=0x1FAFF for c in line):
                        icon_lines.append({'path':path,'line':line_number,'text':line[:700]})
                    if ('theme' in path.lower() or path.endswith('.css')) and re.search(r'currentColor|--(?:bg|fg|panel|border|red|accent|brand|icon)|mask-image|filter:|\.icon',line):
                        color_lines.append({'path':path,'line':line_number,'text':line[:700]})
    finally:
        process.stdin.close()
        process.wait(timeout=20)
    audit = {'upstream':'https://github.com/odysseus-dev/odysseus','ref':ref,'sha':sha,
             'commit':git('show','-s','--format=%cI %s',sha).decode().strip(), 'files_scanned':selected,
             'svg_occurrences':occurrences,'icon_lines':icon_lines,'color_lines':color_lines,
             'routes':[p for p in paths if p.startswith('routes/') and p.endswith('.py')],
             'frontend_modules':[p for p in paths if p.startswith(('static/js/','frontend/src/')) and p.endswith('.js')],
             'summary':{'files':len(selected),'svg_occurrences':len(occurrences),
                        'unique_svg_strings':len({x['hash'] for x in occurrences}),
                        'icon_lines':len(icon_lines),'color_lines':len(color_lines)}}
    (out/'source-audit.json').write_text(json.dumps(audit,ensure_ascii=False,indent=2)+'\n')
    (out/'controls.json').write_text(json.dumps(all_controls,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({'sha':sha,'summary':audit['summary'],'buttons':len(all_controls)},indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo',type=Path,required=True)
    parser.add_argument('--ref',required=True,help='Explicit upstream commit or already-fetched reference')
    parser.add_argument('--out',type=Path,default=Path(__file__).resolve().parents[2]/'docs/icon-suite/evidence')
    args = parser.parse_args()
    capture(args.repo,args.ref,args.out)
