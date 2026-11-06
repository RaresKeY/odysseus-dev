"""Editable 24-unit linework, authored from the generated family references."""

import math


def path(d, **attrs):
    return '<path d="' + d + '"' + ''.join(f' {k.replace("_", "-")}="{v}"' for k, v in attrs.items()) + '/>'


def rect(x=3, y=3, w=18, h=18, rx=2, **attrs):
    return f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}"' + ''.join(f' {k.replace("_", "-")}="{v}"' for k, v in attrs.items()) + '/>'


def circle(x=12, y=12, r=9, **attrs):
    return f'<circle cx="{x}" cy="{y}" r="{r}"' + ''.join(f' {k.replace("_", "-")}="{v}"' for k, v in attrs.items()) + '/>'


def dot(x, y, r=1):
    return circle(x, y, r, fill='currentColor', stroke='none')


def rotate(body, degrees):
    return f'<g transform="rotate({degrees} 12 12)">{body}</g>'


GLYPHS = {}
ALIASES = {}
VARIANTS = {}


def add(name, body):
    if name in GLYPHS:
        raise ValueError(f'Duplicate glyph {name}')
    GLYPHS[name] = body


def alias(base, names):
    for name in names.split():
        ALIASES[name] = base


def variant(name, base, badge):
    VARIANTS[name] = (base, badge)


add('sailboat', path('M3 17h18l-3 4H7l-4-4ZM12 3v14M9 7l-6 7h6V7ZM15 5v9h6L15 5Z'))
add('home', path('m3 11 9-8 9 8M5 10v11h5v-6h4v6h5V10'))
add('chat', path('M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-6 4V6a2 2 0 0 1 2-2Z') + path('M7 9h10M7 13h6'))
add('search', circle(10.5, 10.5, 6.5) + path('m16 16 5 5'))
add('file', path('M5 3h9l5 5v13H5V3ZM14 3v6h5') + path('M8 13h8M8 17h6'))
add('documents', path('M9 3h7l5 5v9M16 3v5h5') + rect(3, 7, 14, 14, 2) + path('M7 12h6M7 16h6'))
add('calendar', rect(3, 5, 18, 16) + path('M7 3v4M17 3v4M3 10h18') + dot(8, 14) + dot(12, 14) + dot(16, 14) + dot(8, 18) + dot(12, 18))
add('compare', rect(3, 5, 7, 14) + rect(14, 5, 7, 14) + path('M12 3v18M6 9h1M6 13h1M17 9h1M17 13h1'))
add('book-open', path('M12 6C9 3 5 3 3 4v15c3-1 6-1 9 2 3-3 6-3 9-2V4c-3-1-6-1-9 2ZM12 6v15M6 8h3M6 12h3M15 8h3M15 12h3'))
add('book-closed', rect(5, 3, 14, 18, 1) + path('M5 17h14M9 3v11M9 7h6'))
add('research', circle(10.5, 10.5, 7.5) + path('m16 16 5 5M7 12l3-5 4 5') + circle(7, 12, 1) + circle(10, 7, 1) + circle(14, 12, 1))
add('email', rect(3, 5, 18, 14) + path('m3 7 9 6 9-6'))
add('image', rect() + circle(16, 8, 2) + path('m3 18 6-7 4 5 3-3 5 6'))
add('library', rect(3, 4, 5, 17, 1) + rect(9, 4, 5, 17, 1) + path('m16 5 3-1 3 15-3 1-3-15M5 8h1M11 8h1M18 8l1-.2'))
add('memory', path('M12 5C9 1 5 3 5 6c-4 0-4 6-2 7-3 4 1 8 5 7 0 3 4 2 4-1V5ZM12 5c3-4 7-2 7 1 4 0 4 6 2 7 3 4-1 8-5 7 0 3-4 2-4-1M6 8c3 0 3 3 1 4M18 8c-3 0-3 3-1 4M5 16c2-2 4-1 4 1M19 16c-2-2-4-1-4 1'))
add('notes', rect(4, 5, 16, 16) + path('M8 3v4M16 3v4M8 11h8M8 15h8M8 18h4'))
add('skills', path('m13 3-9 11h7l-1 7 10-12h-7l1-6Z'))
add('tasks', path('m3 6 2 2 3-4M11 6h10m-18 8 2 2 3-4M11 14h10') + rect(3, 19, 4, 3, .5) + path('M11 20h10'))
add('theme', path('M12 3a9 9 0 1 0 0 18h1a2 2 0 0 0 1-3c-1-2 0-3 2-3h2a4 4 0 0 0 3-4c0-5-5-8-9-8Z') + dot(7, 10) + dot(10, 6.5) + dot(15, 7) + dot(18, 10))
gear = path('m10 3-1 3-3 1-2-1-2 3 2 2v3l-2 2 2 3 3-1 2 1 1 3h4l1-3 3-1 2 1 2-3-2-2v-3l2-2-2-3-3 1-2-1-1-3h-4Z') + circle(12, 12, 3)
add('settings', gear)
add('account', circle(12, 7, 4) + path('M4 21a8 8 0 0 1 16 0H4Z'))
add('folder', path('M3 7V5a2 2 0 0 1 2-2h5l3 3h6a2 2 0 0 1 2 2v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7Z'))
add('terminal', rect(3, 4, 18, 16) + path('M3 8h18m-14 3 4 3-4 3M13 17h4'))
add('integrations', path('M8 3v5M16 3v5M6 8h12v4a6 6 0 0 1-12 0V8ZM12 18v4'))
add('help', circle() + path('M9 9a3 3 0 1 1 5 2c-1 .7-2 1.5-2 3') + dot(12, 18))
add('command-palette', rect() + path('M9 9V7a2 2 0 1 0-2 2h10a2 2 0 1 0-2-2v10a2 2 0 1 0 2-2H7a2 2 0 1 0 2 2V9Z'))
add('notifications', path('M5 17c2-3 2-5 2-8a5 5 0 0 1 10 0c0 3 0 5 2 8H5ZM10 21h4M12 2v2'))

add('add', path('M12 4v16M4 12h16'))
add('subtract', path('M4 12h16'))
add('close', path('m5 5 14 14M19 5 5 19'))
add('check', path('m4 12 5 5L20 6'))
add('copy', rect(8, 8, 13, 13) + path('M16 4V3H3v13h1'))
add('cut', circle(6, 6, 3) + circle(6, 18, 3) + path('m8 8 13 13M8 16 21 3'))
add('paste', rect(5, 5, 14, 16) + rect(8, 3, 8, 4, 1) + path('M9 12h6M9 16h6'))
add('edit', path('m4 16 12-12a2 2 0 0 1 4 4L8 20H4v-4ZM14 6l4 4'))
add('delete', path('M3 6h18M9 6V3h6v3M5 6l1 15h12l1-15M10 10v7M14 10v7'))
add('archive', rect(3, 3, 18, 5, 1) + path('M5 8v13h14V8M9 12h6'))
add('download', path('M12 3v12m-5-5 5 5 5-5M4 16v5h16v-5'))
add('upload', path('M12 15V3m-5 5 5-5 5 5M4 16v5h16v-5'))
add('save', path('M4 3h13l4 4v14H3V4l1-1ZM7 3v6h9V3M7 21v-7h10v7'))
add('refresh', path('M21 4v6h-6M3 20v-6h6M4 9a8 8 0 0 1 13-5l4 4M3 16l4 4a8 8 0 0 0 13-5'))
add('undo', path('m8 4-5 5 5 5M3 9h10a6 6 0 0 1 0 12'))
add('redo', path('m16 4 5 5-5 5M21 9H11a6 6 0 0 0 0 12'))
add('more-horizontal', dot(5, 12, 1.5) + dot(12, 12, 1.5) + dot(19, 12, 1.5))
add('more-vertical', rotate(GLYPHS['more-horizontal'], 90))
add('select-all', rect() + path('m7 12 3 3 7-7'))
add('deselect', rect(3, 3, 18, 18, 2, stroke_dasharray='3 3') + path('m8 8 8 8M16 8l-8 8'))
add('filter', path('M3 4h18l-7 8v7l-4 2v-9L3 4Z'))
add('sort', path('M4 5h16M4 12h11M4 19h6'))
add('sort-ascending', path('M6 20V4m-3 3 3-3 3 3M12 7h4M12 13h7M12 19h10'))
add('sort-descending', path('M6 4v16m-3-3 3 3 3-3M12 5h10M12 11h7M12 17h4'))
add('pin', path('m9 3 6 0-1 6 4 4v2H6v-2l4-4-1-6ZM12 15v7'))
add('favorite', path('m12 3 3 6 6.5 1-4.7 4.7 1.1 6.5-5.9-3.1-5.9 3.1 1.1-6.5L2.5 10 9 9l3-6Z'))
add('unfavorite', GLYPHS['favorite'] + path('m3 3 18 18'))
add('share', circle(18, 5, 3) + circle(6, 12, 3) + circle(18, 19, 3) + path('m9 10 6-4M9 14l6 4'))
add('link', path('m10 7 3-3a5 5 0 0 1 7 7l-3 3M14 17l-3 3a5 5 0 0 1-7-7l3-3M8 16l8-8'))
add('unlink', path('m10 6 2-2a5 5 0 0 1 8 6l-2 2M14 18l-2 2a5 5 0 0 1-8-6l2-2M3 3l18 18'))
add('external-link', path('M14 3h7v7M21 3 10 14M10 3H3v18h18v-7'))
add('move', path('M12 2v20M2 12h20m-13-7 3-3 3 3M9 19l3 3 3-3M5 9l-3 3 3 3m14-6 3 3-3 3'))
add('clear', path('m21 12-8-8-11 11 6 6h6l7-9ZM8 21l-6-6M9 10l8 8M14 21h8'))
add('drag-handle', ''.join(dot(x, y) for x in [9, 15] for y in [5, 12, 19]))
add('arrow-up', path('M12 21V3m-7 7 7-7 7 7'))
for name, angle in [('arrow-right', 90), ('arrow-down', 180), ('arrow-left', 270)]:
    add(name, rotate(GLYPHS['arrow-up'], angle))
add('chevron-up', path('m5 16 7-7 7 7'))
for name, angle in [('chevron-right', 90), ('chevron-down', 180), ('chevron-left', 270)]:
    add(name, rotate(GLYPHS['chevron-up'], angle))
add('chevrons-left', path('m11 5-7 7 7 7m8-14-7 7 7 7'))
add('chevrons-right', rotate(GLYPHS['chevrons-left'], 180))
add('jump-bottom', path('M12 3v13m-6-6 6 6 6-6M4 21h16'))
add('expand', path('M14 3h7v7M21 3l-7 7M10 21H3v-7M3 21l7-7'))
add('collapse', path('M3 3l7 7H3m7 0V3m11 18-7-7h7m-7 0v7'))
add('maximize', rect() + path('M3 8h18'))
add('minimize', path('M5 19h14'))
add('fullscreen', path('M3 9V3h6M15 3h6v6M21 15v6h-6M9 21H3v-6'))
add('fullscreen-exit', path('M9 3v6H3M15 3v6h6M21 15h-6v6M9 21v-6H3'))
add('panel-left', rect() + path('M9 3v18'))
add('panel-right', rect() + path('M15 3v18'))
add('panel-top', rect() + path('M3 9h18'))
add('panel-bottom', rect() + path('M3 15h18'))
add('split-horizontal', rect() + path('M12 3v18'))
add('split-vertical', rect() + path('M3 12h18'))
add('grid', ''.join(rect(x, y, 7, 7, 1) for x in [3, 14] for y in [3, 14]))
add('list', path('M9 5h12M9 12h12M9 19h12') + dot(4, 5) + dot(4, 12) + dot(4, 19))
add('table', rect() + path('M3 9h18M3 15h18M9 3v18M15 3v18'))
add('columns', rect() + path('M8.8 3v18M15.2 3v18'))
add('dock', rect() + path('M3 15h18m-9-9 0 6m-3-3 3 3 3-3'))
add('undock', rect(3, 9, 12, 12, 1) + path('M9 3h12v12M21 3l-9 9'))
add('resize', path('M3 15v6h6M15 3h6v6M3 21l7-7M21 3l-7 7'))

add('send', path('M12 20V4m-6 6 6-6 6 6'))
add('stop', rect(5, 5, 14, 14, 2, fill='currentColor', stroke='none'))
add('branch', circle(6, 5, 2) + circle(6, 19, 2) + circle(18, 5, 2) + path('M6 7v10M18 7v2a5 5 0 0 1-5 5H6'))
add('merge', circle(6, 5, 2) + circle(18, 5, 2) + circle(12, 20, 2) + path('M6 7v2c0 5 6 2 6 9M18 7v2c0 5-6 2-6 9'))
add('quote', path('M4 5h6v7H6c0 4-1 5-3 6M14 5h6v7h-4c0 4-1 5-3 6'))
add('reply', path('m9 5-6 6 6 6M3 11h10a8 8 0 0 1 8 8'))
add('thumbs-up', path('M8 10 12 3c2 0 3 2 2 5l-1 2h7l1 2-3 9H8V10ZM3 10h5v11H3V10Z'))
add('thumbs-down', rotate(GLYPHS['thumbs-up'], 180))
add('thinking', circle(10, 10, 7) + circle(18, 19, 2) + dot(22, 22) + dot(7, 10) + dot(10, 10) + dot(13, 10))
add('reasoning', path('M8 17c0-3-4-4-4-8a8 7 0 0 1 16 0c0 4-4 5-4 8H8ZM9 21h6M9 10l3 3 4-5'))
add('context', circle() + path('M12 3v9h9M7 17l4-4'))
add('compact', path('M3 5h18M6 10h12M9 15h6M11 20h2'))
add('persona', path('M3 6c6-4 12-4 18 0v8c-3 7-15 7-18 0V6ZM7 10h2M15 10h2M8 15c3 2 5 2 8 0'))
add('group-chat', path('M3 4h15v12H8l-5 4V4ZM21 8v13l-5-3h-4'))
add('nobody', GLYPHS['account'] + path('M3 3l18 18'))
add('plan', path('M7 5h14M7 12h14M7 19h14') + dot(3, 5) + dot(3, 12) + dot(3, 19))
add('model', path('m12 3 9 5v9l-9 5-9-5V8l9-5ZM3 8l9 5 9-5M12 13v9'))
add('shuffle', path('M3 5h3c5 0 7 14 12 14h3M17 15l4 4-4 3M3 19h3c2 0 3-2 5-5M13 10c2-3 3-5 5-5h3M17 2l4 3-4 4'))
add('vote', rect(3, 10, 18, 11, 2) + path('M8 14h8M9 3l8 3-3 8-8-3 3-8Z'))
add('trophy', path('M7 3h10v6a5 5 0 0 1-10 0V3ZM7 5H3v3a5 5 0 0 0 5 5M17 5h4v3a5 5 0 0 1-5 5M12 14v7M7 21h10'))
add('scoreboard', rect(3, 5, 18, 15) + path('M12 5v15M7 9h1v7M16 9h2v3h-2v4h2M8 2v3M16 2v3'))
add('tokens', circle(9, 9, 6) + path('M16 6a7 7 0 1 1-10 10M9 6v6M6 9h6'))
add('cost', circle() + path('M15 7H10a3 3 0 0 0 0 6h4a3 3 0 0 1 0 6H8M12 3v18'))
add('prompt', rect(3, 5, 18, 14) + path('m6 9 3 3-3 3M12 15h5'))
add('tool-call', path('m4 4 5 1 2 5-3 3-5-2-1-5M9 13l8 8 4-4-8-8'))
add('tool-result', rect(3, 4, 18, 17) + path('m7 12 3 3 7-7M7 4V2M17 4V2'))
add('approval', path('m12 3 8 3v6c0 5-5 8-8 10-3-2-8-5-8-10V6l8-3Z') + path('m8 12 3 3 5-6'))
add('allow-once', circle() + path('m7 12 3 3 7-7'))
add('allow-session', GLYPHS['chat'] + path('m11 10 2 2 4-4'))
add('deny', circle() + path('m6 6 12 12'))

add('server', rect(3, 3, 18, 7) + rect(3, 14, 18, 7) + dot(7, 6.5) + dot(7, 17.5) + path('M11 6.5h6M11 17.5h6'))
add('endpoint', circle(12, 12, 4) + path('M12 2v6M12 16v6M2 12h6M16 12h6') + dot(12, 2) + dot(22, 12) + dot(12, 22) + dot(2, 12))
add('cpu', rect(6, 6, 12, 12) + rect(9, 9, 6, 6, 1) + path('M9 3v3M15 3v3M9 18v3M15 18v3M3 9h3M3 15h3M18 9h3M18 15h3'))
add('gpu', rect(3, 5, 18, 13) + circle(10, 11.5, 4) + path('M3 21h14M6 18v3M16 8h2M16 12h2M7 11.5h6M10 8.5v6'))
add('ram', rect(3, 6, 18, 12, 1) + path('M6 18v3M10 18v3M14 18v3M18 18v3') + ''.join(rect(x, 9, 3, 6, .5) for x in [6, 11, 16]))
add('disk', path('M6 3h12l3 12v6H3v-6L6 3ZM3 15h18') + dot(17, 18))
add('network', rect(9, 3, 6, 5, 1) + rect(2, 16, 6, 5, 1) + rect(16, 16, 6, 5, 1) + path('M12 8v5M5 16v-3h14v3'))
add('container', rect(3, 6, 18, 12, 1) + path('M7 6v12M12 6v12M17 6v12M3 3h18M3 21h18'))
add('cloud', path('M7 19H6c-2.2 0-4-1.8-4-4s1.8-4 4-4h.2C6.5 6.5 9 4 12.5 4S18.5 6.5 19 10c2 .5 3 2 3 4.5 0 2.5-2 4.5-4.5 4.5H7Z'))
add('database', '<ellipse cx="12" cy="5" rx="9" ry="3"/>' + path('M3 5v14c0 4 18 4 18 0V5M3 12c0 4 18 4 18 0'))
add('usage', path('M3 3v18h18M7 16v-4M12 16V7M17 16v-6'))
add('latency', circle(12, 13, 8) + path('M9 2h6M12 5V2m6 5 2-2M12 13l4-4'))
add('speed', path('M3 18a9 9 0 1 1 18 0M12 15l5-6M6 11l1 1M12 6v2M18 11l-1 1') + dot(12, 15))
add('parameters', path('M5 3v18M12 3v18M19 3v18M2 8h6M9 16h6M16 8h6'))
add('quantization', path('M3 20h4v-5h5v-5h5V4h4M3 5h5M3 10h3'))
add('checkpoint', path('M6 3v19M6 4h14l-4 5 4 5H6'))

add('bold', path('M6 3h7a5 5 0 0 1 0 10H6V3ZM6 13h8a4 4 0 0 1 0 8H6v-8Z'))
add('italic', path('M10 3h10M4 21h10M16 3 8 21'))
add('underline', path('M6 3v9a6 6 0 0 0 12 0V3M4 21h16'))
add('strikethrough', path('M18 6c-3-5-12-4-12 2 0 2 3 4 6 4s6 2 6 4c0 6-9 7-12 2M3 12h18'))
add('heading', path('M4 4v16M16 4v16M4 12h12'))
add('paragraph', path('M18 3v18M14 3v18M14 3H9a5 5 0 0 0 0 10h5M14 3h7'))
add('font-family', path('M3 21 10 3h4l7 18M6 14h12'))
add('font-size', path('M3 5h12M9 5v16M16 11h6M19 11v10'))
add('text-color', path('m5 16 6-13h2l6 13M8 11h8M3 21h18'))
add('highlight', path('m6 13 9-10 6 6-10 9-5-5ZM6 13l-3 5h5M3 22h18'))
add('align-left', path('M3 4h18M3 9h11M3 14h18M3 19h11'))
add('align-center', path('M3 4h18M7 9h10M3 14h18M7 19h10'))
add('align-right', path('M3 4h18M10 9h11M3 14h18M10 19h11'))
add('align-justify', path('M3 4h18M3 9h18M3 14h18M3 19h18'))
add('list-numbered', path('M9 5h12M9 12h12M9 19h12M3 3h1v4M3 10h2v2H3v2h2M3 17h2v4H3M3 19h2'))
add('list-check', path('M10 5h11M10 12h11M10 19h11m-18-15 2 2 3-3m-5 8 2 2 3-3m-5 8 2 2 3-3'))
add('indent', path('M3 4h18M11 9h10M11 14h10M3 20h18M3 8l5 4-5 4'))
add('outdent', path('M3 4h18M11 9h10M11 14h10M3 20h18M8 8l-5 4 5 4'))
add('line-spacing', path('M5 3v18M2 6l3-3 3 3M2 18l3 3 3-3M12 4h9M12 12h9M12 20h9'))
add('code-inline', path('m8 6-6 6 6 6m8-12 6 6-6 6'))
add('code-block', rect(2, 3, 20, 18) + path('M2 7h20m-14 4-4 3 4 3m8-6 4 3-4 3M13 10l-2 8'))
add('separator', path('M3 12h18M7 5h10M7 19h10', stroke_dasharray='2 3'))
add('page-break', path('M4 9V3h16v6M4 15v6h16v-6M2 12h20', stroke_dasharray='3 3'))
add('text-box', rect() + path('M7 8h10M12 8v9M9 17h6'))
add('signature', path('M3 18c4-5 8-17 10-15 2 2-8 17-6 16 2-1 6-9 8-8 1 1-2 6 0 5l3-2 3 2M3 22h18'))
add('find-replace', circle(8, 8, 5) + path('m12 12 4 4M3 19h18m-3-3 3 3-3 3'))
add('spelling', path('m3 13 4-10 4 10M5 9h4m4 9 3 3 6-7'))
add('grammar', path('M3 4h18M3 9h12M3 14h8m1 5 3 3 6-7'))
add('concise', path('M4 4h16M7 9h10M9 14h6m-3 4v4m-3-3 3 3 3-3'))
add('diff', path('M12 3v18M3 7h6M6 4v6M16 7h6M3 15h6M16 15h6M19 12v6'))
add('outline', path('M4 4h16M7 9h13M10 14h10M7 19h13'))
add('print', path('M7 8V3h10v5M6 17H3V9h18v8h-3M7 14h10v7H7v-7Z') + dot(17, 11))
add('word-count', path('M3 5h8M3 10h8M3 15h5M16 3l-4 18M22 3l-4 18M12 9h10M10 15h10'))

add('sticky-note', path('M3 3h18v12l-6 6H3V3ZM15 21v-6h6M7 8h10M7 12h6'))
add('checklist', GLYPHS['tasks'])
add('goal', path('M5 22V3M5 4h15l-4 5 4 5H5'))
add('target', circle() + circle(12, 12, 5) + dot(12, 12))
add('next-step', path('M3 5h6v6h6v6h6M17 13l4 4-4 4'))
add('reminder', GLYPHS['notifications'] + path('M12 7v4l3 2'))
add('tag', path('M3 3h8l10 10-8 8L3 11V3Z') + circle(7, 7, 1))
add('calendar-day', rect(3, 5, 18, 16) + path('M7 3v4M17 3v4M3 10h18M11 14h2v5M10 19h4'))
add('calendar-week', rect(3, 5, 18, 16) + path('M7 3v4M17 3v4M3 10h18M8 13v5M12 13v5M16 13v5'))
add('calendar-month', rect(3, 5, 18, 16) + path('M7 3v4M17 3v4M3 10h18M7 14h10M7 18h10M10 11v10M15 11v10'))
add('clock', circle() + path('M12 6v6l4 2'))
add('timezone', circle(10, 10, 7) + path('M3 10h14M10 3a12 12 0 0 1 0 14M10 3a12 12 0 0 0 0 14') + circle(18, 18, 4) + path('M18 16v2h2'))
add('today', GLYPHS['calendar-day'] + path('M17 14h1', stroke_width='3'))
add('task-queue', path('M3 5h18M3 12h14M3 19h10M17 14l4 4-4 4'))
add('webhook', circle(7, 7, 3) + circle(18, 12, 3) + circle(7, 19, 3) + path('m9 9 6 2M9 17l6-4M4 10v6'))
add('activity', path('M2 12h4l3-8 6 16 3-8h4'))
add('snooze', GLYPHS['clock'] + path('M16 3h6l-6 5h6'))

add('inbox', path('M6 3h12l4 11v7H2v-7L6 3ZM2 14h6l2 4h4l2-4h6'))
add('mail-read', path('M3 8 12 2l9 6v13H3V8ZM3 8l9 7 9-7M3 21l6-7M21 21l-6-7'))
add('reply-all', path('m7 5-5 6 5 6m6-12-5 6 5 6M8 11h5a8 8 0 0 1 8 8'))
add('forward', path('m15 5 6 6-6 6M21 11H11a8 8 0 0 0-8 8'))
add('attachment', path('m21 10-9 9a6 6 0 0 1-9-8l8-8a4 4 0 0 1 6 6l-8 8a2 2 0 0 1-3-3l8-8'))
add('spam', path('m8 3-5 5v8l5 5h8l5-5V8l-5-5H8ZM12 7v6') + dot(12, 17))
add('unsubscribe', GLYPHS['email'] + path('M8 12h8'))
add('translate', path('M3 6h12M9 3v3M13 6c-1 6-5 9-9 11M5 8c1 4 3 6 7 8M13 21l4-10 4 10M15 17h4'))
add('contacts', rect(4, 3, 16, 18) + path('M2 7h3M2 12h3M2 17h3') + circle(12, 9, 3) + path('M7 18a5 5 0 0 1 10 0'))
add('recipients', circle(9, 7, 3) + path('M3 20a6 6 0 0 1 12 0M17 4a3 3 0 0 1 0 6M18 14a5 5 0 0 1 3 6'))
add('outbox', GLYPHS['inbox'] + path('M12 12V3m-3 3 3-3 3 3'))
add('sent', path('m3 3 19 9-19 9 4-9-4-9ZM7 12h15'))
add('drafts', GLYPHS['email'] + path('m8 15 6-6 2 2-6 6H8v-2Z'))
add('inbox-zero', path('M6 3h12l4 11v7H2v-7L6 3ZM2 14h6l2 4h4l2-4h6m-13-6 3 3 5-6'))

add('microphone', rect(8, 2, 8, 13, 4) + path('M5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8'))
add('microphone-off', path('M9 5a4 4 0 0 1 7 3v3M8 9v3a4 4 0 0 0 5 4M5 10v2a7 7 0 0 0 12 5M19 10v2M12 19v3M8 22h8M2 2l20 20'))
add('record', circle() + circle(12, 12, 4, fill='currentColor', stroke='none'))
add('waveform', path('M3 10v4M7 6v12M12 3v18M17 7v10M21 10v4'))
add('volume', path('M3 9h4l5-5v16l-5-5H3V9ZM16 8a6 6 0 0 1 0 8M19 4a11 11 0 0 1 0 16'))
add('volume-off', path('M3 9h4l5-5v16l-5-5H3V9Zm14 0 5 6m0-6-5 6'))
add('headphones', path('M3 13v-2a9 9 0 0 1 18 0v2') + rect(3, 12, 5, 9) + rect(16, 12, 5, 9))
add('play', path('m6 3 15 9-15 9V3Z'))
add('pause', rect(5, 3, 4, 18, 1) + rect(15, 3, 4, 18, 1))
add('skip-next', path('m4 4 12 8-12 8V4ZM20 4v16'))
add('skip-previous', rotate(GLYPHS['skip-next'], 180))
add('repeat', path('m17 2 4 4-4 4M21 6H7a4 4 0 0 0-4 4m4 12-4-4 4-4M3 18h14a4 4 0 0 0 4-4'))
add('camera', path('M3 7h4l2-4h6l2 4h4v14H3V7Z') + circle(12, 13, 4))
add('video', rect(3, 6, 12, 12) + path('m15 10 6-4v12l-6-4'))
add('film', rect() + path('M7 3v18M17 3v18M3 8h4M3 16h4M17 8h4M17 16h4'))
add('transcript', path('M3 7v10M7 3v18M11 8v8M16 5h6M16 11h6M16 17h6'))
add('subtitles', rect(2, 5, 20, 14) + path('M10 10H6v5h4M19 10h-4v5h4'))

add('canvas', path('M3 8V3h5M16 3h5v5M21 16v5h-5M8 21H3v-5M3 12h18M12 3v18', stroke_dasharray='3 2'))
add('crop', path('M6 2v16h16M2 6h16v16'))
add('perspective-crop', path('m6 4 15 3-3 15-15-4L6 4ZM3 2v18h19M2 6h18v16'))
add('rotate-right', path('M21 3v6h-6M21 9l-4-4A8 8 0 1 0 20 17'))
add('rotate-left', '<g transform="translate(24 0) scale(-1 1)">' + GLYPHS['rotate-right'] + '</g>')
add('rotate-half', path('M3 10a9 8 0 0 1 18 0M3 10V4m0 6h6M21 14a9 8 0 0 1-18 0m18 0v6m0-6h-6'))
add('flip-horizontal', path('M12 2v20M9 5 3 19h6V5ZM15 5l6 14h-6V5Z'))
add('flip-vertical', rotate(GLYPHS['flip-horizontal'], 90))
add('zoom-in', GLYPHS['search'] + path('M7 10.5h7M10.5 7v7'))
add('zoom-out', GLYPHS['search'] + path('M7 10.5h7'))
add('zoom-fit', path('M3 8V3h5M16 3h5v5M21 16v5h-5M8 21H3v-5') + rect(7, 7, 10, 10, 1))
add('actual-size', path('M3 8V3h5M16 3h5v5M21 16v5h-5M8 21H3v-5M7 9h2v6M14 9h2v6'))
add('pan', path('M7 11V5a2 2 0 0 1 3 0v5-7a2 2 0 0 1 3 0v7-5a2 2 0 0 1 3 0v6-3a2 2 0 0 1 3 0v8l-4 6h-6l-6-8c-1-2 1-4 3-2l1 1Z'))
add('brush', path('m11 13 8-10c2-2 4 0 2 2l-8 10-2-2ZM11 13c-5-2-6 2-6 4 0 2-2 3-3 3 7 3 12-1 11-5'))
add('eraser', path('m14 3 7 7-11 11H6l-4-4L14 3ZM7 11l8 8M10 22h12'))
add('pen', path('m12 3 9 9-8 8-10 1 1-10 8-8ZM3 21l8-8M7 7l10 10') + circle(12, 12, 1))
add('line', path('M4 20 20 4') + circle(4, 20, 1) + circle(20, 4, 1))
add('path', path('M4 19C4 5 20 19 20 5M4 19V5M20 5v14') + rect(2, 17, 4, 4, .5) + rect(18, 3, 4, 4, .5) + dot(4, 5) + dot(20, 19))
add('polygon', path('m12 3 9 7-4 11H7L3 10l9-7Z'))
add('rectangle', rect(3, 5, 18, 14, 0))
add('ellipse', '<ellipse cx="12" cy="12" rx="10" ry="7"/>')
add('eyedropper', path('m14 4 6 6M13 5l6 6M16 2l6 6-4 4-6-6 4-4ZM13 7 3 17v4h4L17 11'))
add('paint-bucket', path('m6 3 12 12M9 5l-6 6 8 8 9-9-8-8-3 3ZM3 12h15M20 15s-2 4-2 5a2 2 0 0 0 4 0c0-1-2-5-2-5Z'))
add('gradient', rect(3, 5, 18, 14) + path('M7 5v14M11 5v14M15 5v14M18 5v14', opacity='.55') + path('M3 5h4v14H3Z', fill='currentColor', stroke='none'))
add('pattern', ''.join(path(f'm{x} {y} 2-2 2 2-2 2-2-2Z') for x in [3, 10, 17] for y in [5, 12, 19]))
add('swatches', rect(3, 3, 7, 7, 1) + rect(14, 3, 7, 7, 1) + rect(3, 14, 7, 7, 1) + rect(14, 14, 7, 7, 1, fill='currentColor'))
add('color-swap', path('M3 7h15m-4-4 4 4-4 4M21 17H6m4-4-4 4 4 4'))
add('layer', path('m12 3 10 6-10 6L2 9l10-6ZM3 14l9 5 9-5M3 18l9 4 9-4'))
add('layer-group', path('M2 8V5h7l3 3h10v13H2V8Z') + path('m12 10 6 3-6 3-6-3 6-3ZM6 17l6 3 6-3'))
add('merge-down', path('M3 3h18M12 3v11m-5-5 5 5 5-5M3 20h18'))
add('merge-visible', GLYPHS['layer'] + path('M9 8h6M12 5v6'))
add('flatten', path('m12 3 9 5-9 5-9-5 9-5ZM3 17h18M3 21h18'))
add('rasterize', rect(3, 3, 8, 8, 1) + ''.join(rect(x, y, 3, 3, 0, fill='currentColor', stroke='none') for x,y in [(14,3),(19,3),(14,8),(3,14),(8,14),(3,19),(14,14),(19,19)]))
add('lock', rect(5, 10, 14, 11) + path('M8 10V6a4 4 0 0 1 8 0v4M12 15v2'))
add('unlock', rect(5, 10, 14, 11) + path('M8 10V6a4 4 0 0 1 8 0M12 15v2'))
add('eye', path('M2 12c5-11 15-11 20 0-5 11-15 11-20 0Z') + circle(12, 12, 3))
add('eye-off', path('M3 8c5-7 14-6 19 4-1 2-2 3-4 4M14 19c-5 1-9-2-12-7l2-3M3 3l18 18'))
add('mask', rect(3, 3, 18, 18, 1) + circle(12, 12, 5) + path('M3 3h9v4a5 5 0 0 0 0 10v4H3V3Z', fill='currentColor', stroke='none'))
add('selection', rect(3, 3, 18, 18, 1, stroke_dasharray='3 3'))
add('marquee-ellipse', circle(12, 12, 9, stroke_dasharray='3 3'))
add('lasso', '<ellipse cx="12" cy="9" rx="9" ry="6"/>' + path('M7 14c-6 4 1 11 6 6 2-2-3-4-5-2'))
add('magic-wand', path('m3 18 12-12 4 4L7 22l-4-4ZM12 9l4 4M5 3v5M3 5h5M19 2v4M17 4h4M20 17v5M18 19h4'))
add('object-select', path('M3 8V3h5M16 3h5v5M21 16v5h-5M8 21H3v-5m7-14-2 15 4-4 4 5 3-2-4-5 5-1-10-8Z'))
add('subject-select', rect(3, 3, 18, 18, 2, stroke_dasharray='3 3') + circle(12, 9, 3) + path('M6 19a6 6 0 0 1 12 0'))
add('inpaint', rect(3, 3, 18, 18, 2, stroke_dasharray='3 3') + path('m8 16 7-8 2 2-7 8H8v-2Z'))
add('outpaint', rect(7, 7, 10, 10, 1) + path('M3 8V3h5M16 3h5v5M21 16v5h-5M8 21H3v-5', stroke_dasharray='2 2'))
add('background-remove', GLYPHS['image'] + path('M3 3l18 18'))
add('harmonize', circle(8, 12, 5) + circle(16, 12, 5) + path('M9 10h6M9 14h6'))
add('ruler', path('M3 3h18v6H9v12H3V3ZM7 3v3M12 3v3M17 3v3M3 12h3M3 17h3'))
add('guides', path('M7 2v20M17 2v20M2 7h20M2 17h20', stroke_dasharray='3 3'))
add('transform', rect(5, 5, 14, 14, 0) + ''.join(rect(x, y, 4, 4, .5) for x in [2,18] for y in [2,18]))
add('skew', path('M7 4h14l-4 16H3L7 4ZM3 22h18M2 2h20'))
add('perspective', path('m5 3 16 4-4 14-14-4L5 3ZM5 3l12 18M21 7 3 17'))
add('history', circle(12, 12, 9) + path('M3 3v6h6M12 6v7l4 2'))
add('navigator', circle() + path('m8 16 3-7 6-2-3 7-6 2Z'))
add('properties', path('M3 5h18M3 12h18M3 19h18') + circle(8, 5, 2, fill='none') + circle(16, 12, 2, fill='none') + circle(10, 19, 2, fill='none'))
add('channels', rect(3, 3, 10, 10, 1) + rect(11, 11, 10, 10, 1) + path('M14 3h7v7M3 14v7h7'))
add('histogram', path('M3 21V3M3 21h18M6 18v-5h3V7h3v4h3V4h3v14h3'))

add('brightness', circle(12, 12, 4) + path('M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M19 5l-2 2M7 17l-2 2'))
add('contrast', circle() + path('M12 3a9 9 0 0 1 0 18V3Z', fill='currentColor', stroke='none'))
add('exposure', rect(3, 3, 18, 18, 1) + path('M3 21 21 3M6 8h6M9 5v6M14 17h5'))
add('white-balance', path('M9 13V5a3 3 0 0 1 6 0v8a5 4 0 1 1-6 0ZM12 9v9M16 6h3M16 10h3'))
add('hue-saturation', circle(8, 14, 5) + circle(16, 14, 5) + circle(12, 7, 5))
add('vibrance', path('m12 3 9 18H3l9-18ZM12 9l5 9H7l5-9Z'))
add('shadows-highlights', circle(7, 8, 4) + path('M7 4a4 4 0 0 1 0 8Z', fill='currentColor', stroke='none') + path('M17 12v10M12 17h10M4 19h5'))
add('levels', path('M3 21h18M5 18v-5M10 18V7M15 18V3M20 18v-8'))
add('curves', path('M3 3v18h18M3 19C12 19 7 5 15 5h6') + circle(12, 11, 1.4))
add('color-balance', path('M12 3v18M5 21h14M4 7h16M6 7l-4 8h8L6 7ZM18 7l-4 8h8l-4-8Z'))
add('selective-color', circle() + circle(12, 12, 3) + path('M12 3v4M12 17v4M3 12h4M17 12h4'))
add('posterize', ''.join(rect(x, 5, 4, 14, .5, fill='currentColor', opacity=op) for x,op in [(3,'.25'),(10,'.6'),(17,'1')]))
add('threshold', path('M3 20h18M3 20V4M5 17h7V7h9'))
add('photo-filter', GLYPHS['image'] + circle(16, 16, 4))
add('channel-mixer', path('M5 3v18M12 3v18M19 3v18M2 8h6M9 16h6M16 8h6M5 13l7-5 7 7'))
add('color-lookup', path('m12 3 9 5v9l-9 5-9-5V8l9-5ZM3 8l9 5 9-5M12 13v9') + dot(12, 8))
add('color-replace', circle(7, 7, 4) + circle(17, 17, 4) + path('M14 3h7v7m0-7-7 7M3 14v7h7m-7 0 7-7'))

add('blur', circle(12, 12, 3) + circle(12, 12, 7, opacity='.5') + circle(12, 12, 10, opacity='.25'))
add('gaussian-blur', path('M3 20h18M3 18C7 18 7 4 12 4s5 14 9 14'))
add('motion-blur', path('M3 6h11M7 11h14M3 16h10M11 21h10'))
add('zoom-blur', circle(12, 12, 3) + path('M12 2v4M12 18v4M2 12h4M18 12h4M5 5l3 3M16 16l3 3M19 5l-3 3M8 16l-3 3'))
add('radial-blur', path('M12 3a9 9 0 0 1 9 9M21 15a9 9 0 0 1-9 6M9 21a9 9 0 0 1-6-9M3 9a9 9 0 0 1 6-6') + circle(12, 12, 4))
add('box-blur', rect(3, 3, 18, 18, 0, opacity='.3') + rect(6, 6, 12, 12, 0, opacity='.6') + rect(9, 9, 6, 6, 0))
add('surface-blur', rect(3, 3, 18, 18, 4) + path('M7 9c3-3 7-3 10 0M7 15c3-3 7-3 10 0', opacity='.6'))
add('sharpen', path('m3 21 9-18 9 18H3ZM12 3v18'))
add('sharpen-edges', path('m3 21 9-18 9 18H3ZM5 19h14M12 3v18', stroke_width='2.5'))
add('sharpen-more', GLYPHS['sharpen'] + path('m3 9 3-6 3 6M6 3v8'))
add('unsharp-mask', rect(3, 3, 18, 18, 1, stroke_dasharray='3 2') + path('m7 18 5-12 5 12H7Z'))
add('smart-sharpen', GLYPHS['sharpen'] + path('M18 4h4M20 2v4'))
add('noise', ''.join(dot(x,y,1) for x,y in [(3,4),(9,3),(17,5),(21,3),(6,9),(13,10),(21,11),(3,15),(9,17),(17,16),(22,21),(4,21),(14,22)]))
add('despeckle', ''.join(dot(x,y,.7) for x,y in [(4,5),(18,4),(5,17),(20,19)]) + circle(12,12,4))
add('dust-scratches', path('M4 3v8M13 7v14M20 2v11M3 19h4M17 17h5') + dot(8,6) + dot(9,20) + dot(18,13))
add('median', ''.join(rect(x,y,4,4,.5, fill='currentColor', stroke='none', opacity=op) for x,y,op in [(3,3,'.3'),(10,3,'.6'),(17,3,'.3'),(3,10,'.6'),(10,10,'1'),(17,10,'.6'),(3,17,'.3'),(10,17,'.6'),(17,17,'.3')]))
add('pixelate', ''.join(rect(x,y,6,6,0) for x,y in [(3,3),(15,3),(9,9),(3,15),(15,15)]))
add('mosaic', ''.join(rect(x,y,5,5,0, fill='currentColor', opacity=op) for x,y,op in [(3,3,'.25'),(10,3,'.6'),(17,3,'1'),(3,10,'.8'),(10,10,'.3'),(17,10,'.5'),(3,17,'.4'),(10,17,'1'),(17,17,'.2')]))
add('crystallize', path('m3 3 10 2 8-2-2 12 2 6-12-2-6 2V3ZM3 3l6 8 4-6 6 10-10 4V11L3 21M9 11h9'))
add('emboss', path('M3 20V4h16M6 21V7h15M10 11h7v7h-7v-7Z'))
add('find-edges', rect(3,3,18,18,0) + path('M7 17 12 7l5 10H7Z'))
add('glowing-edges', GLYPHS['find-edges'] + path('M2 12h2M20 12h2M12 2v2M12 20v2', opacity='.5'))
add('solarize', GLYPHS['brightness'] + path('M12 8a4 4 0 0 1 0 8V8Z', fill='currentColor', stroke='none'))
add('oil-paint', path('M3 6c7-7 9 6 18-1M3 12c7-7 9 6 18-1M3 18c7-7 9 6 18-1', stroke_width='3'))
add('diffuse', path('M3 4h3M10 7h4M18 3h3M4 13h5M15 14h6M3 21h3M10 18h3M17 21h4', stroke_width='2.5'))
add('wind', path('M3 6h14a2 2 0 1 0-2-2M3 12h17a2 2 0 1 1-2 2M3 18h9a2 2 0 1 1-2 2'))
add('trace-contour', path('M3 21C0 9 7 0 14 3s11 15 5 18M7 21C4 11 9 4 14 7s6 10 2 14M11 21c-3-5-1-11 2-10s3 6 0 10'))
add('halftone', ''.join(dot(x,y,r) for x,r in [(4,.5),(9,.9),(14,1.3),(20,1.7)] for y in [5,12,19]))
add('reticulation', path('M3 5 7 3l4 5 5-4 5 2-4 6 4 6-5 3-5-5-5 5-3-3 4-6-4-7ZM7 3v9l4 4 6-4-1-8'))
add('note-paper', GLYPHS['notes'] + path('M2 8h3M2 13h3M2 18h3'))
add('charcoal', path('m3 20 3-9 4 9 4-15 4 15 3-13', stroke_width='3'))
add('chalk', path('M3 18 16 3l5 5L7 22M12 7l5 5', stroke_dasharray='2 2'))
add('graphic-pen', GLYPHS['pen'] + path('M3 21h18'))
add('photocopy', GLYPHS['copy'] + path('M11 12h7M11 16h7'))
add('water-paper', path('M3 6q5-4 9 0t9 0M3 12q5-4 9 0t9 0M3 18q5-4 9 0t9 0'))
add('poster-edges', GLYPHS['posterize'] + path('M3 3v18M10 3v18M17 3v18'))
add('cutout', path('M3 4h18v16H3V4Zm5 4 8 0-4 8-4-8Z') + path('M3 4l5 4M21 4l-5 4M3 20l9-4M21 20l-9-4'))
add('ink-outlines', GLYPHS['image'] + path('m4 18 5-7 4 5 3-3 4 5', stroke_width='3'))
add('crosshatch', path('M3 8 8 3M3 16 16 3M8 21 21 8M16 21l5-5M3 16l5 5M3 8l13 13M8 3l13 13M16 3l5 5'))
add('stained-glass', rect(3,3,18,18,0) + path('m3 3 8 5 10-5M11 8l-4 8-4 5M7 16l8 5M11 8l10 6M7 16l14-2'))
add('grain', rect() + GLYPHS['noise'])
add('texturizer', path('M3 6h18M3 12h18M3 18h18M6 3v18M12 3v18M18 3v18', stroke_dasharray='3 2'))
add('patchwork', rect(3,3,18,18,0) + path('M3 11h18M11 3v18M3 3l8 8M11 11l10 10M11 3 3 11M21 11 11 21'))
add('craquelure', rect(3,3,18,18,0) + path('m3 7 5 3 3-7M8 10l6 4 7-6M14 14l-1 7M14 14l7 6M8 10l-5 8'))
spiral=[]
for i in range(65):
    a=i/64*math.pi*4.5; r=.3+9*i/64
    spiral.append(('M' if not i else 'L')+f'{12+r*math.cos(a):.2f} {12+r*math.sin(a):.2f}')
add('twirl', path(' '.join(spiral)))
add('pinch', path('M3 3c6 8 6 10 0 18M21 3c-6 8-6 10 0 18M3 3c8 6 10 6 18 0M3 21c8-6 10-6 18 0'))
add('spherize', circle() + '<ellipse cx="12" cy="12" rx="4" ry="9"/>' + '<ellipse cx="12" cy="12" rx="9" ry="4"/>')
add('ripple', path('M3 5q3-4 6 0t6 0 6 0M3 12q3-4 6 0t6 0 6 0M3 19q3-4 6 0t6 0 6 0'))
add('polar-coordinates', circle() + path('M12 3v18M3 12h18M6 6l12 12M18 6 6 18') + circle(12,12,4))
add('lens-correction', path('M3 4q9 4 18 0-4 8 0 16-9-4-18 0 4-8 0-16Z') + path('M12 5v14M5 12h14'))
add('glass', rect(3,3,18,18,0) + path('m8 4-4 7 7-2-2 10 9-5 2 6M17 3l-1 6 5 2'))
add('shear', path('M4 3h16M4 21h16M7 3c14 6-4 12 10 18'))
add('clouds', GLYPHS['cloud'])
add('difference-clouds', GLYPHS['cloud'] + path('M12 5v13M6 12h12'))
add('lens-flare', circle(12,12,3) + path('M12 2v5M12 17v5M2 12h5M17 12h5M5 5l3 3M16 16l3 3M19 5l-3 3M8 16l-3 3') + circle(20,20,2))
add('fibers', path('M4 3v18M8 3l1 18M13 3l-1 18M17 3l1 18M22 3l-1 18', opacity='.7'))
add('offset', rect(3,3,12,12,0) + rect(9,9,12,12,0) + path('M3 21 21 3'))
add('maximum', rect(3,3,18,18,0, fill='currentColor', stroke='none') + rect(9,9,6,6,0, fill='none', stroke='currentColor', opacity='.35'))
add('minimum', rect(3,3,18,18,0) + rect(10,10,4,4,0, fill='currentColor', stroke='none'))
add('color-overlay', rect(3,3,13,13,1) + rect(9,9,12,12,1, fill='currentColor', opacity='.5'))
add('drop-shadow', rect(7,7,14,14,1, fill='currentColor', opacity='.4') + rect(3,3,14,14,1))
add('stroke-effect', rect(3,3,18,18,1) + rect(7,7,10,10,0))
add('haze-removal', GLYPHS['image'] + path('M3 9h18M3 14h8', stroke_dasharray='2 2'))

add('globe', circle() + path('M3 12h18M12 3a16 16 0 0 1 0 18M12 3a16 16 0 0 0 0 18'))
add('browser', rect(2,3,20,18) + path('M2 8h20M6 5.5h.1M10 5.5h.1') + path('M8 12h8M8 16h5'))
add('source', GLYPHS['file'] + path('m8 14 2 2 5-5'))
add('citation', path('M3 5h7v7H6c0 5-2 6-3 7M14 5h7v7h-4c0 5-2 6-3 7'))
add('research-product', path('M3 8h18v13H3V8ZM8 8V6a4 3 0 0 1 8 0v2M8 13h8'))
add('report', GLYPHS['file'] + path('M8 17v-4M11 17v-7M14 17v-2'))
add('research-parallel', path('M3 4h6v16H3V4ZM15 4h6v16h-6V4ZM6 8v8M18 8v8'))
add('research-sequential', path('M3 5h6v6H3V5ZM15 13h6v6h-6v-6ZM9 8h9v5m-3-3 3 3 3-3'))
add('knowledge-base', path('m12 3 9 5v9l-9 5-9-5V8l9-5ZM3 8l9 5 9-5M12 13v9') + circle(12,8,2))
add('rag', GLYPHS['database'] + path('M8 13h8m-4-3 3 3-3 3'))
add('sparkles', path('m10 3 2 6 6 2-6 2-2 6-2-6-6-2 6-2 2-6ZM19 15l1 3 3 1-3 1-1 3-1-3-3-1 3-1 1-3Z'))
add('synapse', circle(12,12,3) + ''.join(circle(x,y,2) for x,y in [(4,4),(20,4),(4,20),(20,20)]) + path('m6 6 4 4m4 4 4 4M6 18l4-4M14 10l4-4'))

add('sliders', GLYPHS['parameters'])
add('font', GLYPHS['font-family'])
add('keyboard', rect(2,5,20,14) + path('M6 9h.1M10 9h.1M14 9h.1M18 9h.1M6 13h.1M10 13h.1M14 13h.1M18 13h.1M7 16h10'))
add('accessibility', circle(12,4,2) + path('M3 9h18M12 7v6m-5 9 5-9 5 9'))
add('density', path('M3 4h18M3 8h18M3 12h18M3 16h18M3 20h18'))
add('mobile', rect(6,2,12,20,2) + path('M10 5h4M11 19h2'))
add('desktop', rect(2,3,20,14,2) + path('M12 17v5M7 22h10'))
add('touch', path('M9 13V4a2 2 0 0 1 4 0v7c1-3 4-2 4 1 2-2 4 0 4 2v3l-4 5h-7l-7-8c-1-2 1-4 3-2l3 2M5 4a7 7 0 0 0-2 5M17 3a7 7 0 0 1 3 5'))
add('info', circle() + path('M12 10v7') + dot(12,6))
add('warning', path('m12 3 10 18H2L12 3ZM12 9v5') + dot(12,18))
add('error', circle() + path('m8 8 8 8M16 8l-8 8'))
add('pending', circle() + path('M12 6v6l3 2'))
add('loading', path('M21 12a9 9 0 1 1-9-9', stroke_dasharray='5 3'))
add('online', path('M3 10a14 14 0 0 1 18 0M6 14a9 9 0 0 1 12 0M9 18a4 4 0 0 1 6 0') + dot(12,21))
add('offline', GLYPHS['online'] + path('M3 3l18 18'))
add('connected', path('m3 8 5 5m0-5 5 5M2 13l5 5 5-5-5-5-5 5ZM22 11l-5-5-5 5 5 5 5-5ZM9 15l6-6'))
add('disconnected', path('m2 13 5 5 4-4-5-5-4 4ZM22 11l-5-5-4 4 5 5 4-4ZM5 3v3M2 6h3M19 18v3M19 18h3'))
add('login', path('M13 3h8v18h-8M3 12h13m-5-5 5 5-5 5'))
add('logout', path('M11 3H3v18h8M9 12h13m-5-5 5 5-5 5'))
add('key', circle(7,8,4) + path('m10 11 10 10M16 17l3-3M13 14l3-3'))
add('token', path('m12 3 9 5v9l-9 5-9-5V8l9-5Z') + circle(12,12,3))
add('shield', path('m12 3 8 3v6c0 5-5 8-8 10-3-2-8-5-8-10V6l8-3Z'))
add('mfa', rect(5,2,14,20,2) + path('M9 6h6M8 15l3 3 5-7'))
add('vault', rect() + circle(12,12,5) + circle(12,12,1.5) + path('M12 7v3M7 12h3M12 14v3M14 12h3M3 7h-1M3 17h-1'))
add('backup', GLYPHS['cloud'] + path('M12 22V11m-4 4 4-4 4 4'))
add('logs', rect(4,3,16,18,1) + path('M8 7h8M8 12h8M8 17h5'))
add('diagnostics', GLYPHS['desktop'] + path('M4 10h3l2-4 3 8 2-4h6'))
add('permissions', GLYPHS['shield'] + path('M9 11h6M9 15h4'))
add('cookies', path('M20 11a5 5 0 0 1-7-7 9 9 0 1 0 7 7Z') + dot(7,10) + dot(9,16) + dot(15,17) + dot(6,15))
add('privacy', GLYPHS['eye'] + path('M12 8v8M8 12h8'))
add('plugin', path('M3 3h6a3 3 0 0 0 6 0h6v6a3 3 0 0 0 0 6v6h-6a3 3 0 0 0-6 0H3v-6a3 3 0 0 0 0-6V3Z'))
add('api', path('m7 5-5 7 5 7m10-14 5 7-5 7M14 3l-4 18'))
add('proxy-routing', rect(2,3,6,6,1) + rect(16,15,6,6,1) + path('M8 6h11v9M16 6l3-3 3 3M5 9v9h11m-3-3 3 3-3 3'))
add('device-pair', rect(2,3,12,11,1) + rect(16,9,6,13,1) + path('M8 14v5M4 19h8M10 8h7m-3-3 3 3-3 3'))

BADGES = {
    'add': path('M19 16v6M16 19h6'),
    'remove': path('M16 19h6'),
    'close': path('m17 17 5 5m0-5-5 5'),
    'check': path('m16 19 2 2 4-5'),
    'download': path('M19 16v6m-3-3 3 3 3-3'),
    'upload': path('M19 22v-6m-3 3 3-3 3 3'),
    'edit': path('m16 20 5-5 2 2-5 5h-2v-2Z'),
    'clock': circle(19,19,4) + path('M19 17v2h2'),
    'play': path('m17 16 6 3-6 3v-6Z'),
    'pause': path('M17 16v6M21 16v6'),
    'lock': rect(16,18,7,5,1) + path('M18 18v-2a1.5 1.5 0 0 1 3 0v2'),
    'search': circle(18,18,3) + path('m20 20 3 3'),
    'spark': path('m19 15 1 3 3 1-3 1-1 3-1-3-3-1 3-1 1-3Z'),
    'refresh': path('M22 16v4h-4m4-1a4 4 0 1 0-3 4'),
    'up': path('M19 22v-6m-3 3 3-3 3 3'),
    'down': path('M19 16v6m-3-3 3 3 3-3'),
    'alert': path('M19 15v4') + dot(19,22),
    'shield': path('m19 15 4 1v3c0 2-2 3-4 4-2-1-4-2-4-4v-3l4-1Z'),
    'chat': path('M15 15h8v6h-4l-4 2v-8Z'),
    'link': path('m16 19 3-3a2 2 0 0 1 3 3l-3 3a2 2 0 0 1-3-3'),
    'dot': dot(19,19,2),
}


def resolve(name):
    if name in GLYPHS:
        return GLYPHS[name]
    if name in VARIANTS:
        base, badge = VARIANTS[name]
        return '<g transform="translate(.3 .3) scale(.72)" stroke-width="2.6">' + resolve(base) + '</g><g transform="translate(1.2 1.2) scale(.9)" stroke-width="2.222">' + BADGES[badge] + '</g>'
    if name in ALIASES:
        return resolve(ALIASES[name])
    raise KeyError(f'No authored geometry for {name}')


# Aliases are intentional shared actions, not generated placeholder glyphs.
alias('folder', 'workspace')
alias('book-open', 'cookbook')
alias('edit', 'rename pencil compose')
alias('download', 'import data-import')
alias('upload', 'export data-export')
alias('refresh', 'regenerate resend reset sync-pending mail-sync launch-retry research-retry')
alias('select-all', 'bulk-select')
alias('external-link', 'open')
alias('copy', 'duplicate')
alias('eye', 'reveal password-show')
alias('eye-off', 'blind-test password-hide')
alias('help', 'ask-user')
alias('clock', 'schedule task-history playback-speed')
alias('account', 'contact')
alias('recipients', 'group-chat-members')
alias('notes', 'notebook')
alias('list', 'list-bulleted')
alias('quote', 'blockquote')
alias('search', 'find web-search')
alias('globe', 'web browser-direct search-provider')
alias('check', 'success accept-change skill-pass skill-approve')
alias('close', 'reject-change')
alias('lock', 'password read-only session-lock')
alias('key', 'api-key ssh')
alias('server', 'provider mcp-server')
alias('parameters', 'develop voice-settings')
alias('usage', 'histogram-bars')
alias('memory', 'embeddings')
alias('theme', 'appearance')
alias('translate', 'language email-translate')
alias('layer-group', 'album')
alias('layer', 'album-cover')
alias('path', 'paths node-edit')
alias('selection', 'marquee-rectangle')
alias('paint-bucket', 'fill')
alias('properties', 'actions-panel')
alias('contrast', 'black-white invert')
alias('gradient', 'gradient-map')
alias('image', 'file-image')
alias('image', 'gallery')
alias('table', 'file-csv')
alias('email', 'file-email')
alias('audio-file', 'file-audio')
alias('film', 'file-video')
alias('font-family', 'file-font')
alias('calendar', 'file-ics')
alias('contacts', 'file-vcf')
alias('logs', 'file-log')
alias('layer', 'file-psd')
alias('api', 'file-code')
alias('integrations', 'mcp plugin-connect')
alias('disconnected', 'plug-disconnect')
alias('tool-call', 'mcp-tool')
alias('online', 'model-online')
alias('offline', 'model-offline search-disabled')
alias('terminal', 'shell-host code-runner')
alias('skills', 'model-capability')
alias('density', 'loading-skeleton')
alias('clipboard-check', 'tool-result-copy')

for name, base, badge in [
    ('new-chat','chat','add'), ('file-new','file','add'), ('folder-new','folder','add'),
    ('folder-open','folder','up'), ('folder-move','folder','up'), ('delete-forever','delete','close'),
    ('restore','archive','up'), ('save-as','save','add'), ('filter-clear','filter','close'),
    ('unpin','pin','close'), ('sidebar-open','panel-left','up'), ('sidebar-close','panel-left','close'),
    ('message-edit','chat','edit'), ('message-delete','chat','close'), ('plan-review','plan','check'),
    ('execute-plan','plan','play'), ('utility-model','model','spark'), ('vision-model','model','search'),
    ('fallback-model','model','refresh'), ('model-switch','model','refresh'), ('compare-add','compare','add'),
    ('prompt-expand','prompt','spark'), ('prompt-inject','prompt','up'), ('background-job','terminal','clock'),
    ('model-add','model','add'), ('model-download','model','download'), ('model-installed','model','check'),
    ('model-delete','model','close'), ('endpoint-add','endpoint','add'), ('endpoint-test','endpoint','check'),
    ('server-add','server','add'), ('server-scan','server','search'), ('server-stop','server','close'),
    ('server-start','server','play'), ('server-config','server','edit'), ('vram','gpu','dot'),
    ('local-model','model','lock'), ('cloud-model','cloud','dot'), ('serving-queue','server','clock'),
    ('document-scan','file','search'), ('table-insert','table','add'), ('table-row-add','table','down'),
    ('table-column-add','table','up'), ('table-row-remove','table','remove'), ('table-column-remove','table','close'),
    ('improve-writing','file','spark'), ('check-sources','source','search'),
    ('note-new','notes','add'), ('note-archive','notes','down'), ('note-color','notes','dot'),
    ('reminder-add','reminder','add'), ('reminder-overdue','reminder','alert'), ('reminder-dismiss','reminder','check'),
    ('tag-add','tag','add'), ('tag-off','tag','close'), ('calendar-add','calendar','add'),
    ('event-add','calendar','add'), ('event-edit','calendar','edit'), ('event-delete','calendar','close'),
    ('event-reschedule','calendar','clock'), ('recurring-event','calendar','refresh'),
    ('recurrence-daily','calendar-day','refresh'), ('recurrence-weekly','calendar-week','refresh'),
    ('recurrence-monthly','calendar-month','refresh'), ('recurrence-yearly','calendar','refresh'),
    ('task-add','tasks','add'), ('task-run','tasks','play'), ('task-paused','tasks','pause'),
    ('task-done','tasks','check'), ('task-failed','tasks','close'), ('task-trigger','tasks','dot'),
    ('task-draft','tasks','edit'), ('mail-unread','email','dot'), ('send-later','sent','clock'),
    ('attachment-download','attachment','download'), ('attachments-download','attachment','down'),
    ('not-spam','email','check'), ('unsubscribe-ignore','unsubscribe','close'), ('email-summarize','email','spark'),
    ('email-extract-event','email','clock'), ('contact-add','account','add'), ('contact-import','contacts','download'),
    ('contact-export','contacts','upload'), ('signature-learn','signature','spark'), ('sender-block','account','close'),
    ('sender-unblock','account','check'), ('email-cleanup','email','spark'), ('speech-to-text','microphone','chat'),
    ('text-to-speech','chat','play'), ('audio-file','file','play'), ('autoplay','play','refresh'),
    ('image-add','image','add'), ('image-import','image','download'), ('image-export','image','upload'),
    ('album-new','album','add'), ('canvas-resize','canvas','up'), ('image-resize','image','up'),
    ('shape-union','rectangle','add'), ('shape-subtract','rectangle','remove'),
    ('shape-intersect','rectangle','check'), ('shape-exclude','rectangle','close'),
    ('layer-new','layer','add'), ('layer-duplicate','layer','dot'), ('layer-delete','layer','close'),
    ('layer-ungroup','layer-group','close'), ('layer-up','layer','up'), ('layer-down','layer','down'),
    ('layer-front','layer','up'), ('layer-back','layer','down'), ('mask-add','mask','add'),
    ('mask-remove','mask','remove'), ('mask-invert','mask','refresh'), ('mask-clear','mask','close'),
    ('mask-refine','mask','edit'), ('magnetic-lasso','lasso','dot'), ('quick-select','selection','spark'),
    ('style-transfer','image','spark'), ('guide-add','guides','add'), ('grid-snap','grid','check'),
    ('snapshot','history','add'), ('auto-tone','contrast','spark'), ('auto-contrast','contrast','check'),
    ('auto-color','hue-saturation','spark'), ('add-noise','noise','add'), ('reduce-noise','noise','remove'),
    ('web-fetch','browser','download'), ('link-check','link','check'), ('source-quality','source','check'),
    ('research-queue','research','clock'), ('research-stop','research','close'), ('research-followup','research','chat'),
    ('memory-add','memory','add'), ('memory-search','memory','search'), ('memory-tidy','memory','spark'),
    ('memory-suggestion','memory','chat'), ('memory-consolidate','memory','check'), ('skill-add','skills','add'),
    ('skill-audit','skills','search'), ('skill-test','skills','play'), ('skill-fail','skills','close'),
    ('skill-confidence','skills','shield'), ('skill-bin','skills','down'), ('skill-restore','skills','up'),
    ('user-add','account','add'), ('user-delete','account','close'), ('user-admin','account','shield'),
    ('user-roles','recipients','lock'), ('token-add','token','add'), ('token-revoke','token','close'),
    ('mfa-enable','mfa','check'), ('vault-lock','vault','lock'), ('vault-unlock','vault','check'),
    ('backup-restore','backup','down'), ('system-update','desktop','download'), ('system-health','desktop','check'),
    ('permission-read','shield','search'), ('permission-write','shield','edit'), ('notification-off','notifications','close'),
    ('data-wipe','database','close'), ('bell-reset','notifications','refresh'), ('integration-add','integrations','add'),
    ('integration-remove','integrations','remove'), ('device-disconnect','device-pair','close'),
    ('network-scan','network','search'), ('mcp-refresh','integrations','refresh'), ('oauth-connect','key','link'),
    ('oauth-revoke','key','close'), ('browser-agent','browser','spark'), ('sidebar-favorite','panel-left','dot'),
    ('workspace-recent','folder','clock'), ('workspace-switch','folder','refresh'), ('workspace-new','folder','add'),
    ('workspace-permissions','folder','shield'), ('workspace-sync','folder','refresh'), ('offline-cache','database','down'),
    ('sync-conflict','refresh','alert'), ('connection-retry','connected','refresh'), ('tool-disabled','tool-call','close'),
    ('tool-waiting','tool-call','clock'), ('context-warning','context','alert'), ('quota-limit','usage','alert'),
    ('budget-limit','cost','alert'), ('data-provenance','source','link'), ('audit-trail','logs','check'),
    ('keyboard-focus','keyboard','dot'), ('keyboard-shortcut','keyboard','spark'),
    ('notification-read','notifications','check'), ('notification-history','notifications','clock'),
    ('batch-progress','tasks','clock'), ('empty-state','inbox','remove'), ('broken-image','image','close'),
    ('unsupported-file','file','close'), ('recover-draft','file','refresh'), ('session-share','chat','link'),
    ('task-dependency','tasks','link'), ('task-priority','tasks','alert'), ('task-cancelled','tasks','close'),
    ('calendar-conflict','calendar','alert'), ('timezone-compare','globe','clock'), ('mail-snoozed','email','clock'),
    ('attachment-blocked','attachment','lock'), ('document-version','file','clock'), ('document-conflict','file','alert'),
    ('document-comment','file','chat'), ('image-compare','image','search'), ('accessible-label','accessibility','chat'),
    ('contrast-check','contrast','check'), ('high-contrast','contrast','add'), ('reduced-motion','activity','remove'),
    ('reset-theme','theme','refresh'), ('color-custom','theme','edit'), ('theme-import','theme','download'),
    ('theme-export','theme','upload'), ('feature-unavailable','plugin','close'),
]:
    variant(name, base, badge)

alias('select-all', 'todo')
alias('pen', 'drawing')
alias('layers-compare', 'image-stack')
alias('brightness', 'theme-light')
alias('approval', 'shield-check fact-check')
alias('compare', 'research-comparison')
alias('checklist', 'research-howto')
alias('grid', 'research-landscape')
alias('histogram', 'equalize')
alias('contrast', 'desaturate')
alias('pattern', 'mosaic-tiles')
alias('ripple', 'ocean-ripple')
alias('bold', 'richtext')
alias('link', 'oauth')
alias('check', 'clipboard-check')

# File-type lettering is actual geometry, so no font or external asset is needed.
LETTERS = {
    'A':'M0 7 2 0l2 7M1 4h2', 'B':'M0 0v7h2q4 0 0-4H0h2q4-3 0-3H0',
    'C':'M4 1Q0-2 0 3.5T4 6', 'D':'M0 0v7h1q6-3.5 0-7H0',
    'E':'M4 0H0v7h4M0 3.5h3', 'F':'M4 0H0v7M0 3h3',
    'G':'M4 1Q0-2 0 3.5T4 6V4H2', 'H':'M0 0v7M4 0v7M0 3.5h4',
    'I':'M0 0h4M2 0v7M0 7h4', 'J':'M4 0v5q0 4-4 0',
    'K':'M0 0v7M4 0 0 3.5 4 7', 'L':'M0 0v7h4',
    'M':'M0 7V0l2 3 2-3v7', 'N':'M0 7V0l4 7V0',
    'O':'M2 0Q0 0 0 3.5T2 7q2 0 2-3.5T2 0Z', 'P':'M0 7V0h2q4 3 0 3H0',
    'Q':'M2 0Q0 0 0 3.5T2 7q2 0 2-3.5T2 0ZM2 5l3 3', 'R':'M0 7V0h2q4 3 0 3H0m2 0 3 4',
    'S':'M4 1Q0-2 0 2q0 1.5 2 1.5T4 5q0 4-4 1', 'T':'M0 0h4M2 0v7',
    'U':'M0 0v5q2 4 4 0V0', 'V':'M0 0l2 7 2-7',
    'W':'M0 0v7l2-3 2 3V0', 'X':'M0 0l4 7M4 0 0 7',
    'Y':'M0 0l2 3.5L4 0M2 3.5V7', 'Z':'M0 0h4L0 7h4',
    '+':'M0 3.5h4M2 1v5', '#':'M1 0v7M3 0v7M0 2h4M0 5h4',
    '1':'M1 1 2 0v7M0 7h4', '2':'M0 1q4-3 4 1L0 7h4',
    '3':'M0 0h4L2 3q4 4-2 4',
}


def letters(text, framed=False):
    width = len(text)*5-1
    scale = min(2, 16/width)
    start = (24-width*scale)/2
    glyph = ''.join(f'<g transform="translate({i*5} 0)">{path(LETTERS[c])}</g>' for i,c in enumerate(text))
    return (rect(2.5,2.5,19,19,2) if framed else '') + f'<g transform="translate({start:.3f} {12-3.5*scale:.3f}) scale({scale:.3f})" stroke-width="{1.8/scale:.3f}">{glyph}</g>'


for name, text in {
    'file-richtext':'A', 'file-markdown':'MD', 'file-javascript':'JS', 'file-typescript':'TS',
    'file-yaml':'YM', 'file-css':'CS', 'file-sql':'SQL', 'file-pdf':'PDF',
    'file-rust':'RS', 'file-go':'GO', 'file-java':'JV', 'file-c':'C',
    'file-cpp':'C+', 'file-csharp':'C#', 'file-ruby':'RB', 'file-php':'PHP',
    'file-toml':'TM', 'file-ini':'INI', 'file-binary':'BIN', 'cc':'CC', 'bcc':'BCC',
}.items():
    add(name, letters(text, framed=name.startswith('file-')))

add('file-python', path('M4 11V7q0-4 4-4h6q4 0 4 4v5H8v3H3v-4h9M20 13v4q0 4-4 4h-6q-4 0-4-4v-5h10v-3h5v4h-9') + dot(8,6,.7) + dot(16,18,.7))
add('file-json', path('M8 3H6v6l-3 3 3 3v6h2M16 3h2v6l3 3-3 3v6h-2'))
add('file-html', GLYPHS['api'])
add('file-xml', GLYPHS['code-inline'] + path('M12 4v16'))
add('file-svg', GLYPHS['path'])
add('file-shell', GLYPHS['terminal'])
add('file-archive', GLYPHS['file'] + path('M11 3v2h2v2h-2v2h2v2h-2v2h2v5h-3v-3h3'))
add('superscript', path('m3 9 8 12M11 9 3 21') + '<g transform="translate(16 2)">' + path(LETTERS['2']) + '</g>')
add('subscript', path('m3 3 8 12M11 3 3 15') + '<g transform="translate(16 15)">' + path(LETTERS['2']) + '</g>')
for number in ['1','2','3']:
    word={'1':'one','2':'two','3':'three'}[number]
    add('heading-'+word, path('M3 4v16M13 4v16M3 12h10') + '<g transform="translate(17 13)">' + path(LETTERS[number]) + '</g>')
add('breadcrumb', path('M3 5h4v4H3V5Zm8 0 4 4-4 4M3 17h4v4H3v-4Zm8-4 4 4-4 4M19 15h3v4h-3v-4Z'))

# Provider identifiers are neutral connector marks, not counterfeit brand logos.
for name, base, text in [
    ('search-searxng','search','S'), ('search-duckduckgo','search','D'),
    ('search-brave','shield','B'), ('search-google','search','G'),
    ('search-tavily','search','T'), ('search-serper','search','R'),
    ('ollama','model','L'), ('openai-compatible','model','A'),
    ('copilot','model','C'), ('chatgpt-subscription','chat','G'),
    ('huggingface','model','H'), ('github','branch','G'),
    ('webdav','folder','W'), ('caldav','calendar','C'),
    ('imap','inbox','I'), ('smtp','sent','S'), ('python-runtime','file-python','P'),
]:
    add(name, '<g transform="translate(.3 .3) scale(.72)" stroke-width="2.6">' + resolve(base) + '</g><g transform="translate(17 15) scale(.9)" stroke-width="2">' + path(LETTERS[text]) + '</g>')


def describe(name):
    if name in VARIANTS:
        base, badge = VARIANTS[name]
        return {'method':'authored base and semantic badge','base':base,'badge':badge}
    if name in ALIASES:
        return {'method':'intentional shared glyph','alias_of':ALIASES[name]}
    return {'method':'authored vector geometry'}


add('warp-text', path('M3 21 10 5h4l7 16M7 15q5-4 10 0M3 4q9-4 18 0'))
add('text-on-path', path('M3 21c4-8 14-8 18 0M5 12l4-9 4 9M7 8h4M15 8h6M18 8v6'))
add('text-on-circle', circle(12,12,9,stroke_dasharray='3 3') + path('m7 16 5-11 5 11M9 12h6'))
add('clear-formatting', path('M5 3h14M12 3l-4 13M3 21l18-14M14 17l6 5m0-5-6 5'))
add('text-direction', path('M3 4h18M3 9h13M3 14h18M3 20h18m-4-4 4 4-4 3'))
add('aspect-ratio', rect(3,5,18,14,1) + path('M7 12V9h4M17 12v3h-4'))
add('crop-square', rect(4,4,16,16,0) + path('M8 2v20M2 8h20',opacity='.45'))
add('crop-portrait', rect(6,2,12,20,0) + path('M9 2v20M6 9h12',opacity='.45'))
add('crop-landscape', rect(2,6,20,12,0) + path('M9 6v12M2 9h20',opacity='.45'))
add('selection-invert', rect(3,3,18,18,1,stroke_dasharray='3 3') + path('M12 4v16M4 4h8v16H4Z',fill='currentColor',stroke='none'))
add('selection-border', rect(3,3,18,18,1,stroke_dasharray='3 3') + rect(7,7,10,10,0))
add('selection-smooth', rect(3,3,18,18,6,stroke_dasharray='3 3') + path('M7 17q0-10 10-10'))
add('selection-feather', rect(3,3,18,18,2,stroke_dasharray='2 3',opacity='.4') + rect(7,7,10,10,2,opacity='.7') + rect(10,10,4,4,1))
add('selection-grow', rect(8,8,8,8,1) + path('m3 8 0-5 5 0M16 3h5v5M21 16v5h-5M8 21H3v-5',stroke_dasharray='2 2'))
add('selection-similar', rect(3,3,7,7,0,stroke_dasharray='2 2') + circle(17,17,4,stroke_dasharray='2 2') + path('m9 15 6-6'))
add('selection-color-range', circle(8,8,5) + circle(16,16,5,stroke_dasharray='2 2') + path('m8 16 8-8'))
add('content-aware-move', path('M3 4h10v10H3V4Z',stroke_dasharray='2 2') + path('M8 9h13m-4-4 4 4-4 4M13 18h8M13 22h8'))
add('stroke-selection', rect(4,4,16,16,1,stroke_width='3') + rect(8,8,8,8,0,stroke_dasharray='2 2'))
add('guides-clear', GLYPHS['guides'] + path('m7 7 10 10M17 7 7 17'))
add('guide-horizontal', path('M2 12h20',stroke_dasharray='3 2') + path('M3 3v18M21 3v18'))
add('guide-vertical', rotate(GLYPHS['guide-horizontal'],90))
add('align-distribute', path('M3 3v18M21 3v18M12 3v18') + rect(6,8,3,8,0) + rect(15,6,3,12,0))
add('transform-width', rect(3,6,18,12,0) + path('M6 12h12m-3-3 3 3-3 3M9 9l-3 3 3 3'))
add('transform-height', rotate(GLYPHS['transform-width'],90))
add('frosted-glass', rect(3,3,18,18,3) + path('M7 6v12M12 6v12M17 6v12',opacity='.35') + path('M5 19 19 5',opacity='.55'))
alias('sparkles','ai-writing image-generate background-effects')
alias('pattern','background-pattern')
alias('crop','free-crop')
alias('selection-grow','selection-expand')
alias('collapse','selection-contract')
alias('image','file-png file-jpeg file-webp')
for name,base,badge in [
    ('brush-preset','brush','dot'), ('selection-add','selection','add'),
    ('selection-subtract','selection','remove'), ('selection-intersect','selection','check'),
    ('selection-save','selection','download'), ('selection-reselect','selection','refresh'),
    ('selection-to-layer','selection','up'), ('selection-to-mask','mask','check'),
]:
    variant(name,base,badge)
