const DATA = JSON.parse(document.getElementById('catalog-data').textContent);
const CONTRACT = JSON.parse(document.getElementById('contract-data').textContent);
const $ = (id) => document.getElementById(id);
const state = { family: 'all', search: '', coverage: 'all', role: 'foreground', theme: 'dark', selected: 'sailboat', view: 'catalog', custom: {} };
const iconById = new Map(DATA.icons.map((icon) => [icon.id, icon]));
const NS = 'http://www.w3.org/2000/svg';

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
      switchView('catalog');
      renderCatalog();
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
  const attributes = { viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', 'stroke-width': '2',
    'stroke-linecap': 'round', 'stroke-linejoin': 'round', role: 'img', 'aria-label': icon.label };
  for (const [name, value] of Object.entries(attributes)) standalone.setAttribute(name, value);
  standalone.append(...Array.from(symbol.childNodes, (node) => node.cloneNode(true)));
  $('svg-code').value = new XMLSerializer().serializeToString(standalone);
  const uses = icon.uses.slice(0, 10).map((use) => {
    const item = document.createElement('li');
    item.textContent = use.label || use.source_kind;
    const link = document.createElement('a');
    const sourcePath = encodeURIComponent(use.path).replaceAll('%2F', '/');
    const line = Number.isSafeInteger(use.line) && use.line > 0 ? use.line : 1;
    link.href = `https://github.com/odysseus-dev/odysseus/blob/${encodeURIComponent(DATA.upstream_sha)}/${sourcePath}#L${encodeURIComponent(String(line))}`;
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
  state.view = view;
  for (const section of document.querySelectorAll('.view')) section.hidden = section.id !== `${view}-view`;
  for (const tab of document.querySelectorAll('[data-view]')) tab.setAttribute('aria-selected', String(tab.dataset.view === view));
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
$('search').addEventListener('input', (event) => { state.search = event.target.value; renderCatalog(); });
$('coverage-select').addEventListener('change', (event) => { state.coverage = event.target.value; renderCatalog(); });
$('role-select').addEventListener('change', (event) => { state.role = event.target.value; renderCatalog(); });
$('size').addEventListener('input', (event) => { $('size-value').value = event.target.value; renderCatalog(); });
document.querySelectorAll('[data-view]').forEach((tab) => tab.addEventListener('click', () => switchView(tab.dataset.view)));
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
window.iconLab = { state, DATA, CONTRACT, applyTheme, refreshTheme, selectIcon, switchView, renderCatalog, filteredIcons, ready: true };
