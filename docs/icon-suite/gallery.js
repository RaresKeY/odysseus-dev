const DATA = JSON.parse(document.getElementById('catalog-data').textContent);
const CONTRACT = JSON.parse(document.getElementById('contract-data').textContent);
const COMPARISON = JSON.parse(document.getElementById('comparison-data').textContent);
const $ = (id) => document.getElementById(id);
const state = { family: 'all', search: '', coverage: 'all', role: 'foreground', theme: 'dark', selected: 'sailboat', view: 'compare', custom: {}, sameInk: true, comparisonScope: 'paired' };
const iconById = new Map(DATA.icons.map((icon) => [icon.id, icon]));
const NS = 'http://www.w3.org/2000/svg';
const CHOICES_KEY = 'odysseus-icon-suite:choices:v1';
let choicesSaved = true;
const reviews = loadChoices();

function variantKey(variant) {
  return variant ? JSON.stringify([variant.original, variant.path, variant.line]) : null;
}

function loadChoices() {
  try {
    const saved = JSON.parse(localStorage.getItem(CHOICES_KEY) || 'null');
    if (!saved || saved.version !== 1 || !saved.choices || typeof saved.choices !== 'object') return {};
    const valid = {};
    for (const icon of DATA.icons) {
      const review = saved.choices[icon.id];
      if (!review || ![null, 'old', 'new'].includes(review.winner)) continue;
      const variants = COMPARISON.icons[icon.id];
      // The source identity, rather than its list position, pins the reviewed variant.
      if (variants.length ? !variants.some(v => variantKey(v) === review.variant) : review.variant !== null) continue;
      if (review.winner === 'old' && !variants.length) continue;
      valid[icon.id] = { winner: review.winner, variant: review.variant };
    }
    return valid;
  } catch {
    choicesSaved = false;
    return {};
  }
}

function saveChoices() {
  try {
    localStorage.setItem(CHOICES_KEY, JSON.stringify({ version: 1, choices: reviews }));
    choicesSaved = true;
  } catch {
    choicesSaved = false;
  }
  refreshChoiceStatus();
}

function refreshChoiceStatus() {
  const count = Object.values(reviews).filter(review => review.winner).length;
  $('winner-count').textContent = `${count} picked`;
  $('choice-save-status').textContent = choicesSaved ? 'Saved in this browser' : 'Cannot save here — export your picks';
  $('choice-save-status').classList.toggle('save-failed', !choicesSaved);
  $('export-choices').disabled = count === 0;
}

function choiceExport() {
  return {
    version: 1,
    upstream_sha: DATA.upstream_sha,
    choices: DATA.icons.filter(icon => reviews[icon.id]?.winner).map(icon => {
      const review = reviews[icon.id];
      const variant = COMPARISON.icons[icon.id].find(v => variantKey(v) === review.variant);
      return { id: icon.id, winner: review.winner, new_svg: `../../static/icons/ui/${icon.id}.svg`,
        old: variant ? { ...variant, source_url: sourceUrl(variant.path, variant.line) } : null };
    }),
  };
}

function glyph(id, size = 24) {
  const svg = document.createElementNS(NS, 'svg');
  svg.setAttribute('class', 'icon');
  svg.setAttribute('viewBox', '0 0 24 24');
  svg.setAttribute('aria-hidden', 'true');
  svg.style.width = `${size}px`;
  svg.style.height = `${size}px`;
  const use = document.createElementNS(NS, 'use');
  use.setAttribute('href', `#ody-${encodeURIComponent(id)}`);
  svg.append(use);
  return svg;
}

function applyTheme(element, name, custom = {}) {
  const colors = { ...CONTRACT.themes[name], ...custom };
  for (const key of ['bg', 'fg', 'panel', 'border', 'red']) element.style.setProperty(`--${key}`, colors[key]);
  const advanced = colors.advanced || {};
  const defaults = { sidebarBg: colors.panel, brandColor: colors.red, brandMixTo: colors.fg,
    hamburgerColor: colors.fg, sendBtnBg: colors.red, sendBtnHover: colors.red,
    userBubbleBg: colors.bg, aiBubbleBg: colors.panel, inputBg: colors.panel, inputBorder: colors.border };
  const variables = { sidebarBg: '--sidebar-bg', brandColor: '--brand-color', brandMixTo: '--brand-mix-to',
    hamburgerColor: '--hamburger-color', sendBtnBg: '--send-btn-bg', sendBtnHover: '--send-btn-hover',
    userBubbleBg: '--user-bubble-bg', aiBubbleBg: '--ai-bubble-bg', inputBg: '--input-bg', inputBorder: '--input-border' };
  for (const [key, css] of Object.entries(variables)) element.style.setProperty(css, advanced[key] || defaults[key]);
  element.style.setProperty('--color-accent', CONTRACT.fixed_tokens['--color-accent']);
  element.dataset.theme = name;
  return colors;
}

function refreshTheme() {
  const colors = applyTheme(document.documentElement, state.theme, state.custom);
  $('fg-color').value = colors.fg;
  $('accent-color').value = colors.red;
  $('bg-color').value = colors.bg;
}

function label(text, className) {
  const el = document.createElement('span');
  el.textContent = text;
  if (className) el.className = className;
  return el;
}

function buildFamilies() {
  const groups = { all: DATA.count, ...DATA.categories };
  for (const [id, count] of Object.entries(groups)) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'family-btn';
    button.dataset.family = id;
    button.classList.toggle('selected', id === state.family);
    button.append(label(id === 'all' ? 'All icons' : id.replaceAll('-', ' ')), label(String(count)));
    button.addEventListener('click', () => {
      state.family = id;
      document.querySelectorAll('.family-btn').forEach((el) => el.classList.toggle('selected', el.dataset.family === id));
      if (!['catalog', 'compare'].includes(state.view)) switchView('compare');
      renderBrowser();
    });
    $('families').append(button);
  }
}

function filteredIcons() {
  const terms = state.search.toLowerCase().trim().split(/\s+/).filter(Boolean);
  return DATA.icons.filter((icon) => (state.family === 'all' || icon.category === state.family)
    && (state.coverage === 'all' || icon.coverage === state.coverage)
    && terms.every((term) => `${icon.id} ${icon.label} ${icon.category} ${icon.aliases.join(' ')}`.toLowerCase().includes(term)));
}

function renderCatalog() {
  const icons = filteredIcons();
  const fragment = document.createDocumentFragment();
  const size = Number($('size').value);
  for (const icon of icons) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'icon-cell';
    button.dataset.id = icon.id;
    button.setAttribute('aria-label', icon.label);
    button.setAttribute('aria-pressed', String(state.selected === icon.id));
    button.title = `${icon.id} · ${icon.coverage}`;
    button.append(glyph(icon.id, size), label(icon.id, 'glyph-name'));
    button.addEventListener('click', () => selectIcon(icon.id));
    fragment.append(button);
  }
  $('icon-grid').replaceChildren(fragment);
  $('icon-grid').dataset.role = state.role;
  $('result-count').textContent = `${icons.length} of ${DATA.count} icons`;
  $('empty-message').hidden = icons.length !== 0;
  return icons.length;
}

const originalTemplates = new Map();
const originalTags = new Set(['svg', 'g', 'path', 'circle', 'ellipse', 'rect', 'line', 'polyline', 'polygon']);
const originalAttributes = new Set(['viewBox', 'd', 'points', 'x', 'y', 'x1', 'x2', 'y1', 'y2', 'cx', 'cy', 'r', 'rx', 'ry', 'width', 'height', 'transform', 'vector-effect', 'fill', 'stroke', 'color', 'stroke-width', 'stroke-linecap', 'stroke-linejoin', 'stroke-dasharray', 'stroke-dashoffset', 'fill-rule', 'opacity', 'fill-opacity', 'stroke-opacity']);
function originalGlyph(id, size) {
  if (COMPARISON.originals[id].kind === 'text') {
    const text = label(COMPARISON.originals[id].text, 'original-text');
    text.style.fontSize = `${Math.min(24, size / 3)}px`;
    return text;
  }
  const key = `${id}:${state.sameInk}`;
  if (!originalTemplates.has(key)) {
    const create = (node) => {
      if (!originalTags.has(node.tag)) throw new Error('Unsupported original SVG element');
      const element = document.createElementNS(NS, node.tag);
      for (const [name, raw] of Object.entries(node.attrs)) {
        if (!originalAttributes.has(name) || /[<>"'{}$;]|url\s*\(|javascript:/i.test(raw)) continue;
        const invisible = ['none', 'transparent'].includes(raw) || /^#(?:[\da-f]{3}0|[\da-f]{6}00)$/i.test(raw) || /^rgba\([^)]*,\s*0(?:\.0+)?\)$/i.test(raw);
        const value = state.sameInk && ['fill', 'stroke', 'color'].includes(name) && !invisible ? 'currentColor' : raw;
        element.setAttribute(name, value);
      }
      if (state.sameInk && node.tag === 'svg' && !node.attrs.fill) element.setAttribute('fill', 'currentColor');
      for (const child of node.children) element.append(create(child));
      return element;
    };
    originalTemplates.set(key, create(COMPARISON.originals[id].tree));
  }
  const svg = originalTemplates.get(key).cloneNode(true);
  svg.setAttribute('class', 'original-icon');
  svg.setAttribute('aria-hidden', 'true');
  svg.style.width = `${size}px`;
  svg.style.height = `${size}px`;
  return svg;
}

function sourceUrl(path, line) {
  const safePath = encodeURIComponent(path).replaceAll('%2F', '/');
  return `https://github.com/odysseus-dev/odysseus/blob/${encodeURIComponent(DATA.upstream_sha)}/${safePath}#L${encodeURIComponent(String(line))}`;
}

function renderComparison() {
  const candidates = filteredIcons();
  const icons = candidates.filter(icon => state.comparisonScope === 'all'
    || (state.comparisonScope === 'paired') === Boolean(COMPARISON.icons[icon.id].length));
  const size = Number($('comparison-size').value);
  const fragment = document.createDocumentFragment();
  for (const icon of icons) {
    const variants = COMPARISON.icons[icon.id];
    const card = document.createElement('article');
    card.className = 'comparison-card';
    card.dataset.id = icon.id;
    const heading = document.createElement('button');
    heading.type = 'button';
    heading.className = 'compare-select';
    heading.setAttribute('aria-label', `Select ${icon.label}`);
    heading.setAttribute('aria-pressed', String(state.selected === icon.id));
    heading.append(label(icon.id), label(icon.category.replaceAll('-', ' '), 'comparison-family'));
    heading.addEventListener('click', () => selectIcon(icon.id));
    const pair = document.createElement('div');
    pair.className = 'comparison-pair';
    const oldSlot = document.createElement('button');
    oldSlot.type = 'button';
    oldSlot.className = 'comparison-slot winner-option old-slot';
    oldSlot.setAttribute('aria-label', `Choose old for ${icon.label}`);
    const oldPreview = document.createElement('span');
    oldPreview.className = 'comparison-preview';
    const oldCaption = label('Choose old', 'winner-caption');
    oldSlot.append(label('OLD', 'comparison-column'), oldPreview, oldCaption);
    const newSlot = document.createElement('button');
    newSlot.type = 'button';
    newSlot.className = 'comparison-slot winner-option new-slot';
    newSlot.setAttribute('aria-label', `Choose new for ${icon.label}`);
    const newPreview = document.createElement('span');
    newPreview.className = 'comparison-preview';
    newPreview.append(glyph(icon.id, size));
    const newCaption = label('Choose new', 'winner-caption');
    newSlot.append(label('NEW', 'comparison-column'), newPreview, newCaption);
    pair.append(oldSlot, newSlot);
    const footer = document.createElement('div');
    footer.className = 'comparison-footer';
    const source = document.createElement('a');
    source.target = '_blank';
    source.rel = 'noopener noreferrer';
    source.setAttribute('aria-label', `View the old source for ${icon.label}`);
    const download = document.createElement('a');
    download.href = `../../static/icons/ui/${encodeURIComponent(icon.id)}.svg`;
    download.download = `${icon.id}.svg`;
    download.textContent = 'New SVG';
    download.setAttribute('aria-label', `Download the new ${icon.label} SVG`);
    const selectVariant = (index) => {
      const variant = variants[index];
      const roles = {'disclosure-arrow':'CARET', 'formatting-glyph':'TYPE MARK', 'numeric-stepper':'STEPPER', 'format-or-mode-label':'FORMAT LABEL', 'menu-command':'MENU LABEL', 'text-command':'TEXT LABEL'};
      oldSlot.querySelector('.comparison-column').textContent = roles[variant.ui_role] ? `OLD · ${roles[variant.ui_role]}` : 'OLD · ICON';
      oldPreview.replaceChildren(originalGlyph(variant.original, size));
      oldPreview.dataset.original = variant.original;
      source.href = sourceUrl(variant.path, variant.line);
      source.textContent = `${variant.path.split('/').pop()}:${variant.line}`;
      source.title = `${variant.source_label} · ${variant.ui_role} · ${variant.basis} · ${variant.path}:${variant.line}`;
    };
    const review = reviews[icon.id];
    let variantIndex = Math.max(0, variants.findIndex(v => variantKey(v) === review?.variant));
    if (variants.length) {
      selectVariant(variantIndex);
      footer.append(source);
    } else {
      oldPreview.append(label('No captured match', 'comparison-missing'));
      footer.append(label(icon.coverage === 'expansion' ? 'Proposed UI' : 'Source match unassigned', 'muted'));
    }
    footer.append(download);
    card.append(heading, pair);
    let select;
    if (variants.length > 1) {
      select = document.createElement('select');
      select.className = 'original-variants';
      select.setAttribute('aria-label', `Old source variant for ${icon.label}`);
      variants.forEach((variant, index) => {
        const option = document.createElement('option');
        option.value = index;
        option.textContent = `${index + 1}/${variants.length} · ${variant.source_label} · ${variant.path.split('/').pop()}:${variant.line}`;
        select.append(option);
      });
      select.value = variantIndex;
      select.addEventListener('change', () => {
        if (reviews[icon.id]?.winner) return;
        variantIndex = Number(select.value);
        selectVariant(variantIndex);
        reviews[icon.id] = { winner: null, variant: variantKey(variants[variantIndex]) };
        saveChoices();
      });
      card.append(select);
    } else {
      card.append(label(variants.length ? variants[0].source_label : 'No original is inferred', 'comparison-basis'));
    }
    const decision = document.createElement('div');
    decision.className = 'comparison-decision';
    const status = label('Not picked', 'winner-status');
    const unlock = document.createElement('button');
    unlock.type = 'button';
    unlock.className = 'small-action unlock-choice';
    unlock.textContent = 'Unlock';
    unlock.setAttribute('aria-label', `Unlock choice for ${icon.label}`);
    const refreshDecision = () => {
      const winner = reviews[icon.id]?.winner;
      card.dataset.winner = winner || '';
      for (const [side, slot, caption] of [['old', oldSlot, oldCaption], ['new', newSlot, newCaption]]) {
        slot.disabled = Boolean(winner) || (side === 'old' && !variants.length);
        slot.setAttribute('aria-pressed', String(winner === side));
        slot.classList.toggle('is-winner', winner === side);
        slot.classList.toggle('is-loser', Boolean(winner && winner !== side && (side === 'new' || variants.length)));
        caption.textContent = winner === side ? 'Winner' : winner ? 'Not chosen' : `Choose ${side}`;
      }
      if (!variants.length) oldCaption.textContent = 'Unavailable';
      if (select) select.disabled = Boolean(winner);
      status.textContent = winner ? `${winner === 'old' ? 'Old' : 'New'} wins · Locked` : 'Not picked';
      unlock.hidden = !winner;
    };
    const choose = (winner) => {
      if (reviews[icon.id]?.winner || (winner === 'old' && !variants.length)) return;
      reviews[icon.id] = { winner, variant: variantKey(variants[variantIndex]) };
      saveChoices();
      refreshDecision();
      unlock.focus({ preventScroll: true });
    };
    oldSlot.addEventListener('click', () => choose('old'));
    newSlot.addEventListener('click', () => choose('new'));
    unlock.addEventListener('click', () => {
      reviews[icon.id] = { winner: null, variant: variantKey(variants[variantIndex]) };
      saveChoices();
      refreshDecision();
      (variants.length ? oldSlot : newSlot).focus({ preventScroll: true });
    });
    decision.append(status, unlock);
    refreshDecision();
    card.append(decision, footer);
    fragment.append(card);
  }
  $('comparison-grid').replaceChildren(fragment);
  $('comparison-grid').dataset.role = state.role;
  $('comparison-count').textContent = `${icons.length} shown`;
  $('comparison-total').textContent = `${COMPARISON.paired_icons} paired / ${DATA.count} new icons`;
  $('comparison-empty').hidden = icons.length !== 0;
  refreshChoiceStatus();
  return icons.length;
}

function renderBrowser() {
  return state.view === 'compare' ? renderComparison() : renderCatalog();
}

function selectIcon(id) {
  const icon = iconById.get(id);
  if (!icon) return;
  state.selected = id;
  $('selected-preview').replaceChildren(glyph(id, 82));
  $('selected-name').textContent = id;
  $('selected-category').textContent = icon.category.replaceAll('-', ' ');
  $('selected-coverage').textContent = icon.coverage;
  $('selected-rationale').textContent = icon.rationale;
  $('selected-method').textContent = icon.geometry.alias_of ? `Shared glyph: ${icon.geometry.alias_of}`
    : icon.geometry.base ? `Composition: ${icon.geometry.base} + ${icon.geometry.badge}` : 'Hand-authored vector geometry';
  const samples = [14, 16, 18, 24, 32, 48].map((size) => {
    const sample = document.createElement('div');
    sample.className = 'size-trial';
    sample.append(glyph(id, size), label(String(size)));
    return sample;
  });
  $('size-trials').replaceChildren(...samples);
  $('download-svg').href = `../../static/icons/ui/${encodeURIComponent(id)}.svg`;
  const symbol = document.getElementById(`ody-${id}`);
  const standalone = document.createElementNS(NS, 'svg');
  for (const [name, value] of Object.entries({viewBox:'0 0 24 24', fill:'none', stroke:'currentColor', 'stroke-width':'2', 'stroke-linecap':'round', 'stroke-linejoin':'round', role:'img', 'aria-label':icon.label})) standalone.setAttribute(name, value);
  standalone.append(...Array.from(symbol.childNodes, node => node.cloneNode(true)));
  $('svg-code').value = new XMLSerializer().serializeToString(standalone);
  const uses = icon.uses.slice(0, 10).map((use) => {
    const item = document.createElement('li');
    item.textContent = use.label || use.source_kind;
    const link = document.createElement('a');
    link.href = sourceUrl(use.path, use.line);
    link.target = '_blank';
    link.rel = 'noopener noreferrer';
    link.textContent = `${use.path.split('/').slice(-2).join('/')}:${use.line}`;
    item.append(link);
    return item;
  });
  if (!uses.length) {
    const item = document.createElement('li');
    item.textContent = icon.coverage === 'expansion' ? 'Proposed UI expansion; no current call site.' : 'Supporting family glyph; select a call site during integration.';
    uses.push(item);
  }
  $('selected-uses').replaceChildren(...uses);
  document.querySelectorAll('.icon-cell').forEach((button) => button.setAttribute('aria-pressed', String(button.dataset.id === id)));
  document.querySelectorAll('.comparison-card').forEach((card) => card.querySelector('.compare-select').setAttribute('aria-pressed', String(card.dataset.id === id)));
  renderThemeTrials();
}

function trialCell(className, id, role) {
  const cell = document.createElement('div');
  cell.className = 'trial-cell';
  cell.dataset.role = role;
  const element = document.createElement(className.includes('btn') ? 'button' : 'span');
  element.className = className;
  element.setAttribute('aria-label', `${role} ${id}`);
  element.append(glyph(id, 20));
  if (role === 'danger') {
    const wrapper = document.createElement('span');
    wrapper.className = 'dropdown-icon';
    wrapper.append(...element.childNodes);
    element.append(wrapper);
  }
  cell.append(element);
  return cell;
}

function renderThemeTrials() {
  const fragment = document.createDocumentFragment();
  for (const [name, colors] of Object.entries(CONTRACT.themes)) {
    const row = document.createElement('div');
    row.className = 'trial-row';
    applyTheme(row, name);
    const nameLabel = document.createElement('span');
    nameLabel.className = 'trial-label';
    nameLabel.textContent = name === 'ocean' ? 'ocean / starfield' : name;
    const sub = document.createElement('small');
    sub.textContent = `${colors.fg} / ${colors.red}`;
    nameLabel.append(sub);
    row.append(nameLabel,
      trialCell('trial-fg', state.selected, 'foreground'),
      trialCell('icon-rail-btn', state.selected, 'rail-idle'),
      trialCell('icon-rail-btn active-section', state.selected, 'rail-active'),
      trialCell('input-icon-btn active', state.selected, 'toolbar'),
      trialCell('send-btn', state.selected, 'send'),
      trialCell('send-btn mic-mode', state.selected, 'send-mic'),
      trialCell('sidebar-brand-title', state.selected, 'brand'),
      trialCell('dropdown-item-danger', state.selected, 'danger'));
    fragment.append(row);
  }
  $('theme-trials').replaceChildren(fragment);
}

function renderCoverage() {
  for (const [coverage, count] of Object.entries(DATA.coverage)) {
    const stat = document.createElement('div');
    stat.className = 'coverage-stat';
    const strong = document.createElement('strong');
    strong.textContent = count;
    stat.append(strong, label(coverage));
    $('coverage-summary').append(stat);
  }
  for (const family of Object.keys(DATA.categories)) {
    const row = document.createElement('tr');
    const counts = DATA.icons.filter((icon) => icon.category === family).reduce((acc, icon) => {
      acc[icon.coverage] = (acc[icon.coverage] || 0) + 1;
      return acc;
    }, {});
    for (const value of [family, ...['existing-svg', 'control-gap', 'supporting', 'expansion'].map((key) => counts[key] || 0)]) {
      const cell = document.createElement('td');
      cell.textContent = value;
      row.append(cell);
    }
    $('coverage-table').append(row);
  }
}

function renderReferences() {
  for (const family of ['navigation', 'actions', 'models-ai', 'authoring', 'image-editor', 'adjustments-effects', 'security-research']) {
    const card = document.createElement('div');
    card.className = 'reference-card';
    const img = document.createElement('img');
    img.src = `references/${family}.png`;
    img.alt = `${family} imagegen reference board`;
    img.loading = 'lazy';
    const caption = document.createElement('p');
    caption.textContent = family.replaceAll('-', ' ');
    const link = document.createElement('a');
    link.href = `references/${family}.prompt.txt`;
    link.textContent = 'Exact generation prompt';
    card.append(img, caption, link);
    $('reference-grid').append(card);
  }
}

function switchView(view) {
  if (!['compare', 'catalog', 'themes', 'coverage', 'references'].includes(view)) view = 'compare';
  state.view = view;
  document.querySelector('.workspace').dataset.activeView = view;
  $('browser-controls').hidden = !['catalog', 'compare'].includes(view);
  $('catalog-size-control').hidden = view === 'compare';
  for (const section of document.querySelectorAll('.view')) section.hidden = section.id !== `${view}-view`;
  for (const tab of document.querySelectorAll('[data-view]')) tab.setAttribute('aria-selected', String(tab.dataset.view === view));
  if (['catalog', 'compare'].includes(view)) renderBrowser();
  history.replaceState(null, '', `#${view}`);
}

for (const [name] of Object.entries(CONTRACT.themes)) {
  const option = document.createElement('option');
  option.value = name;
  option.textContent = name === 'ocean' ? 'Ocean / Starfield' : name[0].toUpperCase() + name.slice(1);
  $('theme-select').append(option);
}
$('theme-select').addEventListener('change', (event) => {
  state.theme = event.target.value;
  state.custom = {};
  refreshTheme();
});
for (const [id, key] of [['fg-color', 'fg'], ['accent-color', 'red'], ['bg-color', 'bg']]) {
  $(id).addEventListener('input', (event) => {
    state.custom[key] = event.target.value;
    refreshTheme();
  });
}
$('reset-theme').addEventListener('click', () => { state.custom = {}; refreshTheme(); });
$('search').addEventListener('input', (event) => { state.search = event.target.value; renderBrowser(); });
$('coverage-select').addEventListener('change', (event) => { state.coverage = event.target.value; renderBrowser(); });
$('role-select').addEventListener('change', (event) => { state.role = event.target.value; renderBrowser(); });
$('size').addEventListener('input', (event) => { $('size-value').value = event.target.value; renderCatalog(); });
document.querySelectorAll('[data-view]').forEach((tab) => tab.addEventListener('click', () => switchView(tab.dataset.view)));
$('comparison-scope').addEventListener('change', event => { state.comparisonScope = event.target.value; renderComparison(); });
$('comparison-size').addEventListener('input', event => { $('comparison-size-value').value = event.target.value; renderComparison(); });
$('same-ink').addEventListener('change', event => { state.sameInk = event.target.checked; renderComparison(); });
$('inspect-toggle').addEventListener('change', event => document.querySelector('.workspace').classList.toggle('compare-inspect', event.target.checked));
$('export-choices').addEventListener('click', () => {
  const url = URL.createObjectURL(new Blob([JSON.stringify(choiceExport(), null, 2) + '\n'], { type: 'application/json' }));
  const link = document.createElement('a');
  link.href = url;
  link.download = 'odysseus-icon-choices.json';
  link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
});
window.addEventListener('hashchange', () => switchView(location.hash.slice(1)));
$('copy-svg').addEventListener('click', async () => {
  try {
    await navigator.clipboard.writeText($('svg-code').value);
    $('toast').textContent = 'SVG copied';
  } catch {
    $('svg-code').focus();
    $('svg-code').select();
    $('toast').textContent = 'SVG selected — copy with your keyboard';
  }
  $('toast').hidden = false;
  setTimeout(() => { $('toast').hidden = true; }, 2400);
});

document.querySelectorAll('[data-icon]').forEach((el) => el.append(glyph(el.dataset.icon)));
$('summary').textContent = `${DATA.count} icons · ${Object.keys(DATA.categories).length} families · 19 themes`;
$('total-count').textContent = DATA.count;
$('source-commit').textContent = `dev@${DATA.upstream_sha.slice(0, 8)}`;
refreshTheme();
buildFamilies();
renderCatalog();
selectIcon(state.selected);
renderCoverage();
renderReferences();
switchView(location.hash.slice(1) || 'compare');
window.iconLab = { state, DATA, CONTRACT, COMPARISON, choiceExport, applyTheme, refreshTheme, selectIcon, switchView, renderCatalog, renderComparison, filteredIcons, ready: true };
