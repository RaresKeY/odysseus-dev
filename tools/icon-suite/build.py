#!/usr/bin/env python3
"""Build the icon inventory, standalone SVGs, sprite and source-backed theme data."""

import ast
import csv
import html
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

from glyphs import describe, resolve
from comparison import build_comparison

ROOT = Path(__file__).resolve().parents[2] / 'docs/icon-suite'
SOURCE = ROOT / 'evidence/upstream-source'

SYNONYMS = {
    'add': ['new', 'create', 'add participant', 'queue'],
    'check': ['apply', 'done', 'ok', 'accept', 'submit', 'verify enable', 'finish', 'got it'],
    'close': ['cancel', 'dismiss', 'no thanks', 'skip', 'clear command', 'remove'],
    'edit': ['edit source', 'edit code', 'rebind', 'change'],
    'search': ['finder', 'search options'], 'more-horizontal': ['more', 'options', 'actions'],
    'eye': ['preview', 'show', 'view', 'toggle selected visibility'], 'eye-off': ['hide', 'hide panel'],
    'refresh': ['reload', 'rescan', 'sync now', 'retry', 'retest', 're test'],
    'play': ['start', 'run', 'execute', 'launch', 'go', 'run now'],
    'select-all': ['all', 'select', 'select multiple', 'select for bulk actions'],
    'deselect': ['none', 'cancel selection'], 'delete': ['bin', 'delete selected', 'permanently delete'],
    'archive': ['view archive'], 'restore': ['unarchive'],
    'copy': ['copy link', 'copy url', 'copy diagnostics', 'copy command', 'copy token', 'copy setup', 'copy email'],
    'save': ['save config', 'save schedule', 'save changes'],
    'settings': ['configure', 'configuration', 'inference settings', 'scan settings', 'ai defaults', 'system'],
    'download': ['install', 'download photo', 'export pdf', 'download pdf'],
    'upload': ['upload from computer', 'import image', 'import files'],
    'arrow-left': ['back', 'go back', 'back to inbox'],
    'chevron-left': ['previous', 'scroll left', 'previous match', 'previous email'],
    'chevron-right': ['next', 'scroll right', 'next match', 'next email'],
    'minimize': ['minimise', 'collapse panel', 'collapse section'],
    'transcript': ['tts', 'tts mode'], 'speech-to-text': ['stt'],
    'attachment': ['attach', 'attach files', 'attach file', 'paperclip'],
    'note-new': ['new note', 'note', 'add note'], 'todo': ['to do', 'to-do', 'mark done'],
    'event-add': ['new event'], 'calendar-add': ['new calendar'],
    'compose': ['new email', 'compose email', 'draft reply', 'draft a reply'],
    'reply-all': ['reply all'], 'send-later': ['schedule email', 'more send options'],
    'skills': ['skill'], 'memory': ['memories'], 'email': ['emails'],
    'library': ['manage chats'], 'server': ['model server', 'model endpoint'],
    'cpu': ['cpu ram only', 'cpu ram'], 'ram': ['memory size'],
    'gpu': ['unified', 'gpu memory'], 'vram': ['gpu memory usage', 'clear server gpu memory'],
    'context': ['ctx', 'context length', 'chat context'], 'context-warning': ['summarize older messages'],
    'gallery': ['photos', 'browse gallery', 'gallery images'],
    'image': ['photo'], 'album': ['albums', 'project'], 'canvas': ['new canvas', 'new image', 'create canvas'],
    'zoom-fit': ['fit view', 'fit view switch to actual size'],
    'pan': ['hand'], 'eraser': ['erase'], 'brush': ['paint'],
    'mask-clear': ['clear mask'], 'mask-invert': ['invert mask'], 'mask-refine': ['refine selection', 'select and mask'],
    'mask': ['quick mask', 'selection overlay'],
    'marquee-rectangle': ['rectangular marquee'], 'marquee-ellipse': ['elliptical marquee'],
    'object-select': ['object selection', 'sam'], 'subject-select': ['subject'],
    'quick-select': ['quick selection'], 'background-remove': ['bg remove', 'remove background'],
    'layer-new': ['new layer', 'new fill layer', 'new adjustment layer', 'import image as layer'],
    'layer-group': ['group selected'], 'layer-front': ['bring to front'], 'layer-up': ['bring forward'],
    'layer-down': ['send backward'], 'layer-back': ['send to back'],
    'merge-down': ['merge down'], 'merge-visible': ['merge all', 'merge layers', 'merge visible'],
    'flatten': ['flatten image', 'flatten copy'], 'rasterize': ['rasterize layer'],
    'lock': ['lock aspect ratio', 'toggle selected lock'],
    'shape-union': ['unite shapes'], 'shape-subtract': ['subtract shapes'],
    'shape-intersect': ['intersect shapes', 'intersect selection'], 'shape-exclude': ['exclude shapes'],
    'inpaint': ['fill masked area', 'remove object', 'retouch', 'erase masked content'],
    'outpaint': ['fill empty areas'], 'harmonize': ['harmonize', 'match latest inpaint'],
    'gradient': ['add stop', 'remove gradient stop', 'gradients'], 'pattern': ['patterns'],
    'text-box': ['add text', 'add text layer'], 'font-family': ['character'],
    'bold': ['strong'], 'italic': ['emphasis'], 'strikethrough': ['strike through'],
    'heading': ['headings'], 'list-bulleted': ['bulleted list'], 'list-numbered': ['numbered list'],
    'blockquote': ['block quote'], 'separator': ['insert separator'], 'table': ['insert table'],
    'signature': ['add signature', 'draw new signature'], 'line-spacing': ['paragraph spacing'],
    'find-replace': ['replace', 'replace all', 'show replace'], 'spelling': ['spelling grammar'],
    'improve-writing': ['suggest improvements', 'ai expand'], 'concise': ['make more concise'],
    'diff': ['compare changes'], 'snapshot': ['create new snapshot'],
    'threshold': ['threshold'], 'black-white': ['black white', 'black and white'],
    'hue-saturation': ['hue saturation'], 'shadows-highlights': ['shadows highlights'],
    'mfa': ['2fa', 'two factor'], 'mfa-enable': ['set up 2fa', 'enable 2fa'],
    'login': ['sign in', 'log in', 'reconnect'], 'logout': ['log out'],
    'token': ['api token'], 'token-add': ['create token'], 'token-revoke': ['revoke'],
    'data-wipe': ['delete everything', 'wipe'], 'notification-off': ['mute'],
    'theme': ['themes', 'customize'], 'color-custom': ['custom color', 'color picker'],
    'reset-theme': ['reset color', 'reset to default', 'reset sidebar', 'reset chat area', 'reset chat bar'],
    'keyboard-shortcut': ['shortcuts', 'keyboard shortcuts'],
    'research-parallel': ['parallel'], 'research-sequential': ['sequential'],
    'research': ['deep research'], 'reasoning': ['reasoning effort'],
    'nobody': ['nobody mode'], 'plan': ['plan mode'], 'prompt': ['system prompt'],
    'persona': ['personas'], 'group-chat': ['group', 'start group chat'],
}

EXTRA_SYNONYMS = {
    'sailboat':['odysseus favicon'], 'sidebar-close':['toggle sidebar'],
    'terminal':['shell access'], 'tool-call':['agent', 'agent tools', 'action'],
    'prompt-inject':['inject'], 'jump-bottom':['scroll to bottom'],
    'model':['added models'], 'reminder':['reminders', 'remind me'],
    'recipients':['users'], 'skill-test':['test'], 'checkpoint':['make default'],
    'delete':['del'], 'play':['resume'], 'task-draft':['draft a task with ai'],
    'calendar':['event', 'date range'], 'error':['errors'], 'task-done':['completed'],
    'schedule':['clock', 'in 1 hour', 'in 3 hours', 'tomorrow 9am', 'monday 9am'],
    'ai-writing':['writing tools', 'match my writing style'], 'font-size':['editor display size'],
    'code-inline':['code'], 'align-left':['text alignment'], 'word-count':['words in document'],
    'server-stop':['graceful', 'sigterm', 'force', 'sigkill'],
    'image-compare':['original', 'show document before editing'], 'calendar-day':['this event only', 'monday', 'sunday'],
    'chat':['chats', 'discuss'], 'container':['docker'], 'download':['pip', 'dependencies'],
    'sliders':['set hardware manually', 'using manual hardware'], 'favorite':['favorites'],
    'regenerate':['re roll'], 'search':['scan further'], 'attachment':['attachments'],
    'image-generate':['generate', 'generate now'], 'clear':['clean'],
    'source':['extract'], 'info':['what does extract do'], 'paste':['from clipboard'],
    'paint-bucket':['fill with foreground color', 'fill'], 'properties':['adjustments'],
    'rotate-right':['rotate 90 cw', 'rotate 90 degrees'], 'rotate-left':['rotate 90 ccw','rotate 1'],
    'rotate-half':['rotate 180'], 'transform':['arbitrary'],
    'hue-saturation':['rgb color'], 'black-white':['grayscale'],
    'feature-unavailable':['cmyk needs a color managed pipeline','lab needs a color managed pipeline','3d','3d is not available in this build','plugins are not available in this build'],
    'channels':['bits channel'], 'selection-reselect':['reselect'], 'selection-border':['border'],
    'selection-smooth':['smooth'], 'selection-contract':['contract'], 'selection-feather':['feather'],
    'selection-grow':['grow'], 'selection-similar':['similar'], 'selection-color-range':['color range'],
    'stroke-effect':['stroke retained'], 'sharpen':['crisp detail'], 'drop-shadow':['soft shadow'],
    'filter':['retained effects','presets','stylize','sketch','artistic','texture','distort','render','other','custom'],
    'ruler':['rulers'], 'grid-snap':['snap'], 'layer':['layers'], 'plugin':['plugins'],
    'fullscreen':['distraction free'], 'panel-right':['window panels'],
    'workspace':['essentials','photography','painting','graphic web','minimal'],
    'transform-width':['decrease width','increase width'], 'transform-height':['decrease height','increase height'],
    'aspect-ratio':['swap width and height'],
    'align-distribute':['align or distribute'], 'color-swap':['swap colors'],
    'recurrence-daily':['daily'], 'recurrence-weekly':['weekly'],
    'recurrence-monthly':['monthly','every month','nth weekday'], 'recurrence-yearly':['yearly'],
    'ellipse':['circle click'], 'drawing':['draw'], 'crop-square':['square'],
    'crop-portrait':['portrait','story'], 'crop-landscape':['landscape','widescreen'],
    'canvas':['a4','4k','default 1920'], 'file-png':['png'], 'file-jpeg':['jpeg'],
    'file-webp':['webp'], 'file-psd':['psd'],
}
for _name, _phrases in EXTRA_SYNONYMS.items():
    SYNONYMS.setdefault(_name, []).extend(_phrases)

CATEGORY_PATHS = {
    'navigation': ['static/index.html'],
    'actions': ['static/js/ui.js', 'static/js/modalManager.js'],
    'layout': ['static/js/modalManager.js', 'static/js/tileManager.js'],
    'chat-ai': ['static/js/chat.js', 'static/js/chatRenderer.js', 'static/js/compare/'],
    'models-hardware': ['static/js/cookbook', 'static/js/providers.js', 'static/js/models.js'],
    'documents': ['static/js/document.js', 'static/js/documentLibrary.js'],
    'notes-tasks-calendar': ['static/js/tasks.js', 'static/js/calendar.js', 'static/js/features/notes/'],
    'email-contacts': ['static/js/emailLibrary/', 'static/js/emailInbox.js', 'static/js/signature.js'],
    'audio-video': ['static/js/voice', 'static/js/tts', 'static/js/fileHandler.js'],
    'gallery-editor': ['static/js/gallery', 'static/js/editor/'],
    'image-adjustments': ['static/js/editor/'], 'image-filters': ['static/js/editor/'],
    'research-knowledge': ['static/js/research/', 'static/js/skills.js', 'static/js/memory.js'],
    'settings-security': ['static/js/settings', 'static/js/admin.js', 'static/login.html'],
    'file-types': ['static/js/langIcons.js'],
    'integrations-providers': ['static/js/settings', 'static/js/providers.js', 'static/js/searchProviderIcons.js'],
    'expanded-ui': [],
}

REFERENCE_FAMILIES = {
    'navigation': 'navigation', 'actions': 'actions', 'layout': 'actions',
    'chat-ai': 'models-ai', 'models-hardware': 'models-ai',
    'documents': 'authoring', 'notes-tasks-calendar': 'authoring', 'email-contacts': 'authoring',
    'audio-video': 'authoring', 'gallery-editor': 'image-editor',
    'image-adjustments': 'adjustments-effects', 'image-filters': 'adjustments-effects',
    'research-knowledge': 'security-research', 'settings-security': 'security-research',
    'file-types': 'authoring', 'integrations-providers': 'models-ai', 'expanded-ui': 'security-research',
}


def normalized(text):
    text = html.unescape(re.sub(r'<[^>]*>', ' ', text)).lower()
    return ' '.join(re.findall(r'[a-z0-9]+', text))


def object_literal(source, start):
    begin = source.index('{', start)
    depth = 0
    quote = None
    escaped = False
    for i in range(begin, len(source)):
        c = source[i]
        if quote:
            if escaped:
                escaped = False
            elif c == '\\':
                escaped = True
            elif c == quote:
                quote = None
        elif c in ('"', "'"):
            quote = c
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if depth == 0:
                return source[begin:i+1]
    raise ValueError('Unclosed object')


def theme_data():
    source = (SOURCE/'static/js/theme.js').read_text()
    literal = object_literal(source, source.index('export const THEMES'))
    literal = re.sub(r'(?m)^\s*//[^\n]*', '', literal)
    literal = re.sub(r'\b([A-Za-z][\w]*)\s*:', r'"\1":', literal)
    themes = ast.literal_eval(literal)
    assert len(themes) == 19
    return themes


def css_rules():
    source_path = 'static/css/01-agent-chat.css'
    source = (SOURCE/source_path).read_text()
    selectors = ['.icon-rail-btn', '.icon-rail-btn:hover', '.icon-rail-btn.active-section',
                 '.input-icon-btn', '.input-icon-btn:hover', '.input-icon-btn.active,',
                 '.send-btn', '.send-btn:hover', '.send-btn.mic-mode,',
                 '.sidebar-brand-title', '.sidebar-hamburger', '.dropdown-item-danger',
                 '.dropdown-item-danger .dropdown-icon']
    rules = []
    for selector in selectors:
        match = re.search(r'(?m)^\s*'+re.escape(selector)+r'\s*(?:\n[^{}]*|)\{', source)
        if not match:
            raise ValueError(f'Missing upstream CSS selector {selector}')
        end = source.index('}', match.end())+1
        rules.append({'selector':selector, 'path':source_path,
                      'line':source.count('\n', 0, match.start())+1,
                      'css':source[match.start():end].strip()})
    return rules


def main():
    plan = json.loads((Path(__file__).resolve().parent/'design/icon-plan.json').read_text())
    audit = json.loads((ROOT/'evidence/source-audit.json').read_text())
    controls = json.loads((ROOT/'evidence/controls.json').read_text())
    icons = plan['icons']
    by_id = {x['id']: x for x in icons}
    for icon in icons:
        icon.update({'aliases':SYNONYMS.get(icon['id'], []), 'uses':[], 'svg':'../../static/icons/ui/'+icon['id']+'.svg',
                     'geometry':describe(icon['id']), 'reference_board':REFERENCE_FAMILIES[icon['category']],
                     'reference_scope':'family style and motifs; compositions and semantic aliases are manually authored'})

    lexicon = []
    for icon in icons:
        for phrase in [icon['id'].replace('-', ' '), *icon['aliases']]:
            lexicon.append((normalized(phrase), icon['id']))

    def match_icon(text, path):
        text = ' '+normalized(text)+' '
        best = None
        score = 0
        for phrase, ident in lexicon:
            if not phrase or ' '+phrase+' ' not in text:
                continue
            current = len(phrase.split())*10 + len(phrase)/100
            if text.strip() == phrase:
                current += 50
            if any(path.startswith(p) for p in CATEGORY_PATHS[by_id[ident]['category']]):
                current += 4
            if by_id[ident]['category'] == 'expanded-ui':
                current -= 2
            if current > score:
                best, score = ident, current
        return best, round(score, 2)

    control_map = []
    for control in controls:
        if any(s in control['path'] for s in ['test-fixtures/', '-variants.html', 'variants.js']):
            control_map.append({**control, 'icon':None, 'disposition':'prototype or fixture; outside production UI'})
            continue
        ident, score = match_icon(control['label'], control['path'])
        if not ident:
            ident, score = match_icon(control['id'], control['path'])
        entry = {**control, 'icon':ident, 'match_score':score,
                 'disposition':'candidate mapping; review at integration' if ident else 'text, dynamic content or specialised control; see source label'}
        control_map.append(entry)
        if ident:
            by_id[ident]['uses'].append({'path':control['path'], 'line':control['line'], 'label':control['label'],
                                       'source_kind':'literal SVG' if control['has_svg'] else 'dynamic icon' if control['dynamic_icon'] else 'control without literal icon',
                                       'confidence':'exact label' if score >= 50 else 'label-based candidate'})

    svg_map = []
    for occurrence in audit['svg_occurrences']:
        ident, score = match_icon(occurrence['context'], occurrence['path'])
        svg_map.append({'path':occurrence['path'], 'line':occurrence['line'], 'source_hash':occurrence['hash'],
                        'icon':ident, 'match_score':score,
                        'disposition':'context-based candidate' if ident else 'raw occurrence retained; dynamic/decorative content needs integration review'})
        if ident and not any(u['path'] == occurrence['path'] and abs(u['line']-occurrence['line']) < 3 for u in by_id[ident]['uses']):
            by_id[ident]['uses'].append({'path':occurrence['path'], 'line':occurrence['line'],
                                       'source_kind':'SVG context', 'confidence':'context-based candidate'})

    # These dynamic shape registries need direct source evidence rather than button-label matching.
    theme_source = (SOURCE/'static/js/theme.js').read_text()
    boat = next((n for n, line in enumerate(theme_source.splitlines(), 1) if "M16 4L16 22L6 22Z" in line), None)
    if boat:
        by_id['sailboat']['uses'].append({'path':'static/js/theme.js','line':boat,'label':'Default sailboat favicon',
                                        'source_kind':'literal SVG','confidence':'source inspected'})
    for path, prefix, replacements in [
        ('static/js/langIcons.js','file-',{'richtext':'richtext','bash':'shell','sh':'shell'}),
        ('static/js/searchProviderIcons.js','search-',{'google_pse':'google'}),
    ]:
        source = (SOURCE/path).read_text()
        if path.endswith('langIcons.js'):
            source = source.split('const ALIASES')[0]
        for match in re.finditer(r'^\s{2}([a-z][a-z0-9_]*):', source, re.M):
            key = match.group(1)
            ident = prefix+replacements.get(key,key)
            if ident in by_id:
                by_id[ident]['uses'].append({'path':path,'line':source.count('\n',0,match.start())+1,
                                            'label':'Dynamic glyph registry: '+key,'source_kind':'literal SVG','confidence':'source inspected'})

    for icon in icons:
        uses = icon['uses']
        icon['coverage'] = ('expansion' if icon['category'] == 'expanded-ui' else
                            'existing-svg' if any(u['source_kind'] in ['literal SVG','dynamic icon','SVG context'] for u in uses) else
                            'control-gap' if uses else 'supporting')
        icon['rationale'] = ('Proposed affordance for expanded UI; availability is not a product commitment.' if icon['coverage']=='expansion' else
                             'Candidate for a current control without a literal SVG; text-only controls may be intentional.' if icon['coverage']=='control-gap' else
                             'Supporting glyph for this implemented subsystem; exact integration location remains to be selected.' if icon['coverage']=='supporting' else
                             'Unifies an icon concept already represented in the current interface.')

    out = ROOT/'../../static/icons/ui'
    out.mkdir(parents=True, exist_ok=True)
    symbols = []
    for icon in icons:
        ident = icon['id']
        body = resolve(ident)
        standalone = f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" role="img" aria-labelledby="{ident}-title"><title id="{ident}-title">{html.escape(icon["label"])}</title>{body}</svg>\n'
        (out/(ident+'.svg')).write_text(standalone)
        symbols.append(f'<symbol id="ody-{ident}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">{body}</symbol>')
    sprite = '<svg xmlns="http://www.w3.org/2000/svg"><defs>'+''.join(symbols)+'</defs></svg>\n'
    (out/'sprite.svg').write_text(sprite)

    themes = theme_data()
    rules = css_rules()
    contract = {'upstream_sha':audit['sha'], 'themes':themes, 'source':'static/js/theme.js:13',
                'roles':{'foreground':'var(--fg)', 'accent':'var(--accent, var(--red))',
                         'rail':'var(--accent, var(--red)) at 0.5 opacity; hover/active opacity 1',
                         'rail_active_background':'color-mix(in srgb, var(--color-accent) 15%, transparent)',
                         'brand':'var(--brand-color, var(--red))', 'hamburger':'var(--hamburger-color, var(--fg))',
                         'send':'#fff on var(--send-btn-bg, var(--red))',
                         'send_mic':'var(--fg) on color-mix(in srgb, var(--send-btn-bg, var(--red)) 30%, var(--panel))',
                         'danger':'var(--red)', 'danger_icon_opacity':0.7},
                'fixed_tokens':{'--color-accent':'#00aaff'}, 'css_rules':rules,
                'limits':['Component color harness, not a running Odysseus server.',
                          'Production styles do not define --accent globally; fallbacks and invalid no-fallback color-mix declarations are preserved.',
                          'Source-label icon mapping is a candidate replacement map, not an automatic integration patch.',
                          'Provider glyphs are neutral identifiers, not official vendor logos.']}
    (ROOT/'theme-contract.json').write_text(json.dumps(contract, indent=2)+'\n')
    (ROOT/'upstream-components.css').write_text('/* Exact selected rules from Odysseus '+audit['sha']+'; see theme-contract.json for source lines. */\n'+
                                             '\n\n'.join(x['css'] for x in rules)+'\n')
    inventory = {'upstream':audit['upstream'], 'upstream_sha':audit['sha'], 'upstream_commit':audit['commit'],
                 'scan':{**audit['summary'], 'buttons':len(controls)},
                 'count':len(icons), 'categories':dict(Counter(x['category'] for x in icons)),
                 'coverage':dict(Counter(x['coverage'] for x in icons)),
                 'scope':plan['scope'], 'icons':icons}
    (ROOT/'catalog.json').write_text(json.dumps(inventory, ensure_ascii=False, indent=2)+'\n')
    (ROOT/'evidence/control-map.json').write_text(json.dumps(control_map, ensure_ascii=False, indent=2)+'\n')
    (ROOT/'evidence/svg-occurrence-map.json').write_text(json.dumps(svg_map, indent=2)+'\n')
    with (ROOT/'catalog.csv').open('w', newline='') as f:
        writer = csv.writer(f, lineterminator='\n')
        writer.writerow(['id','label','category','coverage','svg','reference_board','alias_of','observed_uses','rationale'])
        for x in icons:
            writer.writerow([x['id'],x['label'],x['category'],x['coverage'],x['svg'],x['reference_board'],
                             x['geometry'].get('alias_of',''),len(x['uses']),x['rationale']])
    lines = ['# Odysseus icon inventory','', f'Basis: upstream `dev@{audit["sha"]}` ({audit["commit"]}).', '',
             f'{len(icons)} SVG files across {len(plan["groups"])} families. The scan covers {audit["summary"]["files"]} source files, {len(controls)} button occurrences and {audit["summary"]["svg_occurrences"]} SVG occurrences.', '',
             'Existing SVG concepts, candidate gaps on current controls, supporting subsystem glyphs and proposed UI expansions are distinguished. A text-only button is not automatically a defect. Shared concepts deliberately reuse geometry; the catalog records aliases and composed badges. Every entry has its own editable SVG.', '',
             'The source occurrence and control maps retain every scanned occurrence, including unclassified dynamic/decorative markup. Exact replacement mapping still needs an integration review; the source scan is exhaustive for the stated snapshot, while future UI needs remain a curated proposal.', '']
    for category, ids in plan['groups'].items():
        lines += ['## '+category.replace('-',' ').title(), '', '| Icon | Coverage | Source uses |', '| --- | --- | --- |']
        for ident in ids:
            x = by_id[ident]
            lines.append(f'| [{ident}]({x["svg"]}) | {x["coverage"]} | {len(x["uses"])} |')
        lines += ['']
    (ROOT/'ICON_INVENTORY.md').write_text('\n'.join(lines).rstrip()+'\n')
    template = (ROOT/'template.html').read_text()
    comparison = build_comparison(ROOT, inventory, audit, SOURCE)
    inline_sprite = sprite.replace('<svg xmlns="http://www.w3.org/2000/svg">', '<svg xmlns="http://www.w3.org/2000/svg" aria-hidden="true" style="position:absolute;width:0;height:0;overflow:hidden">', 1)
    template = template.replace('<!-- SPRITE -->', inline_sprite)
    template = template.replace('<!-- DATA -->', json.dumps(inventory, ensure_ascii=False).replace('<', '\\u003c'))
    template = template.replace('<!-- CONTRACT -->', json.dumps(contract).replace('<', '\\u003c'))
    template = template.replace('<!-- COMPARISON -->', json.dumps(comparison, ensure_ascii=False).replace('<', '\\u003c'))
    template = template.replace('<!-- APP -->', (ROOT/'gallery.js').read_text())
    (ROOT/'index.html').write_text(template)
    print(json.dumps({'icons':len(icons), 'coverage':inventory['coverage'], 'themes':len(themes),
                      'mapped_controls':sum(x['icon'] is not None for x in control_map),
                      'controls_retained':len(control_map), 'svg_occurrences_retained':len(svg_map)}, indent=2))


if __name__ == '__main__':
    main()
