"""Recover static old SVGs and label-grounded comparison candidates from the pin."""
import hashlib
import html
import json
import re
import xml.etree.ElementTree as ET
from collections import defaultdict
from source_roles import role_of

NS = 'http://www.w3.org/2000/svg'
TAGS = {'svg', 'g', 'path', 'circle', 'ellipse', 'rect', 'line', 'polyline', 'polygon'}
PAINT = {'fill', 'stroke', 'color', 'stroke-width', 'stroke-linecap', 'stroke-linejoin',
         'stroke-dasharray', 'stroke-dashoffset', 'fill-rule', 'opacity', 'fill-opacity', 'stroke-opacity'}
ATTRS = PAINT | {'viewBox', 'd', 'points', 'x', 'y', 'x1', 'x2', 'y1', 'y2', 'cx', 'cy',
                 'r', 'rx', 'ry', 'width', 'height', 'transform', 'vector-effect'}


def words(text):
    text = re.sub(r'([a-z])([A-Z])', r'\1 \2', text)
    return re.sub(r'[^a-z0-9]+', ' ', html.unescape(text).lower()).strip()


def vector_tree(markup):
    """Keep inert vector primitives only; dynamic geometry is not reconstructed."""
    root = ET.fromstring(markup)

    def convert(node):
        tag = node.tag.split('}')[-1]
        if tag in {'title', 'desc'}:
            return None
        if tag not in TAGS:
            raise ValueError('Unsupported SVG element: '+tag)
        attributes = dict(node.attrib)
        for declaration in attributes.pop('style', '').split(';'):
            name, _, value = declaration.partition(':')
            if name.strip() in PAINT:
                attributes[name.strip()] = value.strip()
        clean = {}
        for name, value in attributes.items():
            if name not in ATTRS:
                continue
            if name in {'fill', 'stroke', 'color'} and re.fullmatch(r'\$\{[^}]+\}', value):
                value = 'currentColor'
            if re.search(r'[<>\"\'{}$;]|url\s*\(|javascript:', value, re.I):
                raise ValueError('Dynamic geometry or external paint')
            if not re.fullmatch(r'[\w#().,%+\s-]+', value):
                raise ValueError('Unsupported SVG attribute')
            clean[name] = value
        children = [converted for child in node if (converted := convert(child)) is not None]
        return {'tag': tag, 'attrs': clean, 'children': children}

    tree = convert(root)
    if tree['tag'] != 'svg':
        raise ValueError('Not an SVG')
    if 'viewBox' not in tree['attrs']:
        width = float(re.sub('px$', '', tree['attrs'].get('width', '24')))
        height = float(re.sub('px$', '', tree['attrs'].get('height', '24')))
        tree['attrs']['viewBox'] = f'0 0 {width:g} {height:g}'
    if len(tree['attrs']['viewBox'].split()) != 4:
        raise ValueError('Invalid viewBox')
    def shapes(node):
        return int(node['tag'] not in {'svg', 'g'}) + sum(shapes(child) for child in node['children'])
    if not shapes(tree):
        raise ValueError('No static geometry')
    return tree


def serialize(tree):
    node = ET.Element(tree['tag'], tree['attrs'])
    for child in tree['children']:
        node.append(ET.fromstring(serialize(child)))
    return ET.tostring(node, encoding='unicode')


def build_comparison(root, inventory, audit, source):
    canonical, aliases = defaultdict(set), defaultdict(set)
    icons = {icon['id']: icon for icon in inventory['icons']}
    for icon in inventory['icons']:
        if icon['coverage'] == 'expansion':
            continue
        for phrase in [icon['id'], icon['label']]:
            canonical[words(phrase)].add(icon['id'])
        for phrase in icon['aliases']:
            aliases[words(phrase)].add(icon['id'])

    def identify(text, allow_phrase=False):
        normalized = words(text)
        if not normalized or '${' in text:
            return None
        candidates = canonical.get(normalized) or aliases.get(normalized)
        if candidates:
            return next(iter(candidates)) if len(candidates) == 1 else None
        if not allow_phrase:
            return None
        matches = []
        verbs = {'add', 'create', 'new', 'delete', 'remove', 'clear', 'edit', 'rename', 'run', 'test', 'generate', 'extract', 'copy', 'paste', 'save', 'load', 'import', 'export', 'download', 'upload'}
        tokens = set(normalized.split())
        for phrase, ids in canonical.items():
            phrase_tokens = set(phrase.split())
            if len(ids) == 1 and phrase_tokens <= tokens and not (tokens & verbs)-phrase_tokens:
                ident = next(iter(ids))
                if icons[ident]['category'] == 'navigation' and len(phrase_tokens) == 1:
                    qualifiers = {'open', 'show', 'view', 'manage', 'manager', 'panel', 'window', 'toggle', 'hide', 'the', 'your', 'my', 'all', 'a', 'an', 'and'}
                    if tokens-phrase_tokens-qualifiers:
                        continue
                matches.append((len(phrase.split()), len(phrase), ident))
        if not matches:
            return None
        matches.sort(reverse=True)
        best = [row for row in matches if row[:2] == matches[0][:2]]
        return best[0][2] if len({row[2] for row in best}) == 1 else None

    grouped = defaultdict(list)
    for occurrence in audit['svg_occurrences']:
        if re.search(r'fixture|prototype|(?:^|/)tests?/', occurrence['path'], re.I):
            continue
        grouped[occurrence['path']].append(occurrence)
    variants, originals, rejected = defaultdict(dict), {}, 0
    literal_uses = {(use['path'], use['line']): icon['id'] for icon in inventory['icons']
                    for use in icon['uses'] if use.get('source_kind') == 'literal SVG'
                    and use.get('confidence') == 'source inspected'}
    for path, occurrences in grouped.items():
        if (source/path).exists():
            text = (source/path).read_text()
            spans = list(re.finditer(r'<svg\b[^>]*>.*?</svg>', text, re.S | re.I))
            buttons = list(re.finditer(r'<button\b([^>]*)>(.*?)</button>', text, re.S | re.I))
            inputs = [(text, buttons, occurrence, span) for occurrence, span in zip(occurrences, spans, strict=True)]
        else:
            inputs = []
            # The retained audit also covers root app.js, which was not copied into the browser snapshot.
            for occurrence in occurrences:
                text = occurrence['context']
                matches = []
                for span in re.finditer(r'<svg\b[^>]*>.*?</svg>', text, re.S | re.I):
                    digest = hashlib.sha256(re.sub(r'\s+', ' ', span.group()).encode()).hexdigest()[:12]
                    if digest == occurrence['hash']:
                        matches.append(span)
                if matches:
                    span = min(matches, key=lambda match:abs(match.start()-180))
                    buttons = list(re.finditer(r'<button\b([^>]*)>(.*?)</button>', text, re.S | re.I))
                    inputs.append((text, buttons, occurrence, span))
        for text, buttons, occurrence, span in inputs:
            before = text[max(0, span.start()-180):span.start()]
            ident, basis, priority, source_label = None, '', 0, ''
            if (path, occurrence['line']) in literal_uses:
                ident, basis, priority = literal_uses[path, occurrence['line']], 'Inspected literal', 100
                source_label = icons[ident]['label']
            if not ident:
                for button in buttons:
                    if button.start() <= span.start() and span.end() <= button.end():
                        # A trailing chevron must not inherit the main button's action label.
                        if '<svg' in text[button.start():span.start()]:
                            break
                        for attr in ['aria-label', 'title']:
                            match = re.search(r'\b'+attr+r'\s*=\s*([\"\'])(.*?)\1', button.group(1), re.S)
                            if match and (candidate := identify(match.group(2), True)):
                                ident, basis, priority = candidate, 'Button '+attr, 90
                                source_label = html.unescape(match.group(2))
                                break
                        break
            if not ident:
                named = re.search(r'([A-Za-z_][\w-]*)\s*[=:]\s*[\"\'`]\s*$', before)
                if named and words(named.group(1)) not in {'svg', 'icon', 'html', 'content', 'markup', 'text'}:
                    ident = identify(re.sub(r'\b(?:icon|svg|html)\b', ' ', words(named.group(1))), True)
                    if ident:
                        basis, priority = 'Named SVG: '+named.group(1), 80
                        source_label = named.group(1)
            if not ident:
                continue
            try:
                tree = vector_tree(span.group())
            except (ET.ParseError, ValueError):
                rejected += 1
                continue
            digest = occurrence['hash']
            if digest not in originals:
                originals[digest] = {'id':digest, 'kind':'svg', 'tree':tree, 'svg':'originals/'+digest+'.svg'}
            candidate = {'original':digest, 'path':path, 'line':occurrence['line'], 'basis':basis, 'source_label':source_label,
                         'priority':priority, 'status':'source-label candidate'}
            previous = variants[ident].get(digest)
            if not previous or candidate['priority'] > previous['priority']:
                variants[ident][digest] = candidate
    for path in sorted(grouped):
        if not (source/path).exists():
            continue
        text = (source/path).read_text()
        for button in re.finditer(r'<button\b([^>]*)>(.*?)</button>', text, re.S | re.I):
            if re.search(r'<svg\b', button.group(2), re.I):
                continue
            visible = html.unescape(re.sub(r'<[^>]*>', '', button.group(2)))
            visible = re.sub(r'\s+', ' ', visible).strip()
            if not re.fullmatch(r'[A-Za-z0-9 .+_/-]{1,32}', visible):
                continue
            caption = visible
            for attr in ['aria-label', 'title']:
                match = re.search(r'\b'+attr+r'\s*=\s*([\"\'])(.*?)\1', button.group(1), re.S)
                if match:
                    caption = match.group(2)
                    break
            ident = identify(caption, True)
            if not ident:
                continue
            digest = 'text-'+hashlib.sha256(visible.encode()).hexdigest()[:12]
            originals[digest] = {'id':digest, 'kind':'text', 'text':visible}
            if digest not in variants[ident]:
                variants[ident][digest] = {'original':digest, 'path':path,
                    'line':text.count('\n', 0, button.start())+1, 'basis':'Literal button text', 'source_label':html.unescape(caption),
                    'priority':60, 'status':'source-label candidate'}
    rows = {ident: sorted(variants[ident].values(), key=lambda x:(-x['priority'], x['path'], x['line']))
            for ident in icons}
    lines_by_path = {}
    for entries in rows.values():
        for entry in entries:
            path = entry['path']
            if path not in lines_by_path:
                lines_by_path[path] = (source/path).read_text().splitlines() if (source/path).exists() else []
            lines = lines_by_path[path]
            context = '\n'.join(lines[max(0, entry['line']-4):entry['line']+2])
            entry['ui_role'] = role_of(originals[entry['original']], entry['source_label'], context)
    result = {'upstream_sha':inventory['upstream_sha'], 'count':len(icons),
              'paired_icons':sum(bool(value) for value in rows.values()),
              'originals':originals, 'icons':rows, 'dynamic_candidates_omitted':rejected,
              'scope':'Static source SVG geometry and explicit paint. Surrounding app CSS/animations are not replayed. Mappings use source labels or named literals and remain contextual candidates.'}
    target = root/'originals'
    target.mkdir(exist_ok=True)
    for stale in target.glob('*.svg'):
        if re.fullmatch(r'[0-9a-f]{12}\.svg', stale.name) and stale.stem not in originals:
            stale.unlink()
    for original in originals.values():
        if original['kind'] != 'svg':
            continue
        svg = serialize(original['tree']).replace('<svg ', f'<svg xmlns="{NS}" ', 1)
        (root/original['svg']).write_text(svg+'\n')
    (root/'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
    return result
