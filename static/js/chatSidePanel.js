// One shared tab strip follows the attached pane; each feature owns its content.
const panels = new Map();
let tabs, toggle, last = 'files';

function current() {
  return [...panels.values()].find(item => item.pane?.open);
}

function sync() {
  const active = current();
  if (active) {
    last = active.id;
    const header = active.pane.querySelector('header,.modal-header');
    if (tabs.parentElement !== header) header.prepend(tabs);
  }
  tabs.hidden = !active;
  toggle.setAttribute('aria-expanded', String(!!active));
  toggle.title = active ? 'Collapse side panel' : 'Open side panel';
  if (active) toggle.setAttribute('aria-controls', active.pane.id);
  for (const item of panels.values()) {
    const selected = active === item;
    item.trigger.setAttribute('aria-pressed', String(selected));
    item.trigger.tabIndex = selected ? 0 : -1;
  }
}

function init() {
  if (tabs) return;
  tabs = document.getElementById('chat-panel-tabs');
  toggle = document.getElementById('chat-panel-toggle');
  toggle.addEventListener('click', () => {
    const active = current();
    if (active) active.pane.querySelector('[data-panel-close]').click();
    else {
      const target = panels.get(last);
      (target && !target.trigger.disabled ? target : panels.get('files'))?.trigger.click();
    }
    sync();
  });
  tabs.addEventListener('keydown', event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    const available = [...panels.values()].filter(item => !item.trigger.disabled);
    const index = available.findIndex(item => item.trigger === event.target);
    if (index < 0) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? available.length - 1
      : (index + (event.key === 'ArrowRight' ? 1 : -1) + available.length) % available.length;
    available[next].trigger.click();
    sync();
    available[next].trigger.focus();
  });
}

export function registerChatPanel(id, trigger, pane = null) {
  init();
  let item = panels.get(id);
  if (!item) {
    item = {id, trigger, pane: null};
    panels.set(id, item);
    // Re-selecting a tab keeps its content open; the common toggle collapses it.
    trigger.addEventListener('click', event => {
      if (item.pane?.open) { event.preventDefault(); event.stopImmediatePropagation(); }
    }, true);
    tabs.append(trigger);
  }
  if (pane && pane !== item.pane) {
    item.pane = pane;
    new MutationObserver(sync).observe(pane, {attributes: true, attributeFilter: ['open']});
  }
  sync();
}

export function chatPanelToggle() {
  return document.getElementById('chat-panel-toggle');
}
