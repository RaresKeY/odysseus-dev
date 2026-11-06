"""Classify the captured affordance independently of its proposed icon name."""
import re


def role_of(original, source_label, context=''):
    if original['kind'] == 'text':
        if re.search(r'Increase|Decrease|Skew|Perspective', source_label) and original['text'] in {'+', '-'}:
            return 'numeric-stepper'
        if re.search(r'Bold|Italic|Underline|Strikethrough|Superscript|Subscript', source_label):
            return 'formatting-glyph'
        if original['text'] in {'PNG', 'JPEG', 'WebP', 'PSD', 'RGB Color', 'Grayscale'}:
            return 'format-or-mode-label'
        return 'menu-command' if 'dropdown-item' in context else 'text-command'
    children = original['tree']['children']
    if len(children) == 1 and children[0]['tag'] in {'path', 'polyline'}:
        shape = children[0]['attrs'].get('d', children[0]['attrs'].get('points', ''))
        compact = re.sub(r'\s+', '', shape.lower())
        if compact in {'691215189', 'm691215189', 'm69666-6', 'm69l666-6', '6151291815', '1815129615'}:
            return 'disclosure-arrow'
        if compact == '1518912156' and 'collapse' in source_label.lower():
            return 'disclosure-arrow'
    return 'action-or-status-icon'
