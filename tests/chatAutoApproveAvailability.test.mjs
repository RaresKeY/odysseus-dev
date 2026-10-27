import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

// Execute the production controller with inert session/activity imports. The
// small DOM adapter models control state, events and focus, not visual layout.
const source = readFileSync(new URL('../static/js/chatAutomation.js', import.meta.url), 'utf8')
  .replace(/^import .*;\n/gm, '').replace('export async function ', 'async function ');
const markup = readFileSync(new URL('../static/index.html', import.meta.url), 'utf8');
const ready = { available: true, reason_code: null, reason: null };
const missing = { available: false, reason_code: 'decision_provider_not_configured', reason: 'Configure a decision model in Chat settings.' };
const noKey = { available: false, reason_code: 'decision_credentials_missing', reason: 'Configure the Jev API key in the registered endpoint.' };
const settle = () => new Promise(setImmediate);
function deferred() {
  let resolve;
  const promise = new Promise(done => { resolve = done; });
  return { promise, resolve };
}

function fixture({ availability = missing, mode = 'always_ask', omitAvailability = false } = {}) {
  const all = [], nodes = new Map();
  let document;
  class Element {
    constructor(tag = 'div', attrs = {}) {
      this.tag = tag; this.attrs = attrs; this.children = []; this.listeners = new Map();
      this.dataset = {}; this.style = {}; this.disabled = 'disabled' in attrs;
      this.hidden = 'hidden' in attrs; this.open = false; this.textContent = ''; this.value = '';
      this.classList = { toggle() {} }; this.offsetWidth = 310; this.offsetHeight = 360;
      for (const [key, value] of Object.entries(attrs)) this.setAttribute(key, value);
      all.push(this);
    }
    setAttribute(key, value) {
      this.attrs[key] = String(value);
      if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = String(value);
    }
    getAttribute(key) { return this.attrs[key] ?? null; }
    append(...children) {
      for (const child of children) {
        if (child.parent) child.parent.children = child.parent.children.filter(item => item !== child);
        this.children.push(child); child.parent = this;
      }
    }
    replaceChildren(...children) { this.children = []; this.append(...children); }
    descendants() { return this.children.flatMap(child => [child, ...child.descendants()]); }
    matches(selector) {
      if (selector.startsWith('button') && this.tag !== 'button') return false;
      if (selector.includes(':not(:disabled)') && this.disabled) return false;
      if (selector.includes(':not([hidden])') && this.hidden) return false;
      const plain = selector.replaceAll(':not(:disabled)', '').replaceAll(':not([hidden])', '');
      for (const [, key, value] of plain.matchAll(/\[([^=\]]+)(?:="([^"]*)")?\]/g)) {
        if (!(key in this.attrs) || (value !== undefined && this.attrs[key] !== value)) return false;
      }
      return true;
    }
    querySelectorAll(selector) { return this.descendants().filter(node => node.matches(selector)); }
    querySelector(selector) { return this.querySelectorAll(selector)[0] ?? null; }
    addEventListener(type, listener) {
      if (!this.listeners.has(type)) this.listeners.set(type, []);
      this.listeners.get(type).push(listener);
    }
    removeEventListener(type, listener) { this.listeners.set(type, (this.listeners.get(type) || []).filter(fn => fn !== listener)); }
    dispatchEvent(event) {
      event.target ??= this; event.currentTarget = this;
      event.preventDefault ??= () => {}; event.stopPropagation ??= () => {};
      return Promise.all((this.listeners.get(event.type) || []).map(listener => listener(event)));
    }
    click() { return this.disabled ? Promise.resolve() : this.dispatchEvent({ type: 'click' }); }
    focus() { if (!this.disabled) document.activeElement = this; }
    contains(node) { return node === this || this.descendants().includes(node); }
    getBoundingClientRect() { return { top: 600, left: 20 }; }
    showModal() { this.open = true; }
    close() { this.open = false; this.dispatchEvent({ type: 'close' }); }
  }
  // Use real control identifiers and attributes from index.html, rather than
  // duplicating the disabled/ARIA defaults that this regression protects.
  for (const [, tag, raw] of markup.matchAll(/<(\w+)\b([^>]*\bid="[^"]+"[^>]*)>/g)) {
    const attrs = Object.fromEntries([...raw.matchAll(/([\w-]+)(?:="([^"]*)")?/g)].map(([, key, value]) => [key, value ?? '']));
    nodes.set(attrs.id, new Element(tag, attrs));
  }
  const menu = nodes.get('chat-permissions-menu'), control = nodes.get('chat-permissions-control');
  const modes = {};
  for (const [, raw] of markup.matchAll(/<button\b([^>]*\bdata-approval-mode="[^"]+"[^>]*)>/g)) {
    const attrs = Object.fromEntries([...raw.matchAll(/([\w-]+)(?:="([^"]*)")?/g)].map(([, key, value]) => [key, value ?? '']));
    const node = new Element('button', attrs); modes[node.dataset.approvalMode] = node; menu.append(node);
  }
  menu.append(nodes.get('chat-auto-approve-unavailable'), nodes.get('chat-auto-approve-setup'));
  control.append(nodes.get('chat-approval-mode'), menu);
  document = Object.assign(new Element(), {
    readyState: 'loading', hidden: false, body: new Element('body'), activeElement: null,
    getElementById: id => nodes.get(id), createElement: tag => new Element(tag),
    createTextNode: text => Object.assign(new Element(), { textContent: text }),
    querySelector: selector => all.find(node => node.matches(selector)),
  });
  document.body.append(control);
  const window = Object.assign(new Element(), { innerWidth: 1200, innerHeight: 800 });
  const state = { availability, mode, revision: 0, sessionId: 'chat', omitAvailability,
    policyGate: null, failRefresh: false, rejectActivation: false };
  const requests = [];
  const projection = () => state.omitAvailability ? {} : { auto_approve_availability: structuredClone(state.availability) };
  const policy = () => ({ session_id: 'chat', approval_mode: state.mode, policy_revision: state.revision,
    tool_routing: 'existing', ...projection() });
  const respond = (value, ok = true) => ({ ok, json: async () => value });
  const context = vm.createContext({ document, window,
    sessions: { getCurrentSessionId: () => state.sessionId },
    CustomEvent: class { constructor(type, fields) { this.type = type; Object.assign(this, fields); } },
    Event: class { constructor(type) { this.type = type; } },
    Option: class extends Element { constructor(text, value) { super('option'); this.textContent = text; this.value = value; } },
    fetch: async (path, options) => {
      const body = options.body ? JSON.parse(options.body) : undefined;
      requests.push({ path, method: options.method, body });
      if (path === '/api/chat-settings/models') return respond({ endpoints: [], decision_endpoints: [] });
      if (options.method === 'GET' && state.failRefresh) throw new Error('Settings temporarily unavailable');
      if (path === '/api/chat-settings') return respond({ revision: 0, ...projection() });
      assert.equal(path, '/api/session/chat/automation');
      if (options.method === 'PUT') {
        if (body.changes.approval_mode === 'auto_approve' && state.rejectActivation) {
          state.availability = noKey;
          return respond({ detail: noKey.reason }, false);
        }
        state.mode = body.changes.approval_mode ?? state.mode; state.revision++;
      }
      const response = policy();
      if (options.method === 'GET' && state.policyGate) {
        const gate = state.policyGate; state.policyGate = null;
        await gate.promise;
      }
      return respond(response);
    },
  });
  vm.runInContext(source, context, { filename: 'chatAutomation.js' });
  return { context, document, window, nodes, modes, state, requests,
    initialized: document.dispatchEvent({ type: 'DOMContentLoaded' }),
    writes: () => requests.filter(request => request.method === 'PUT'),
  };
}

for (const [name, options] of [
  ['no reviewer', { availability: missing }], ['missing Jev key', { availability: noKey }],
  ['missing readiness metadata', { omitAvailability: true }],
]) {
  test(`Auto-Approve stays visible and disabled with ${name}`, async () => {
    const h = fixture(options);
    assert.equal(h.modes.auto_approve.disabled, true);
    await h.initialized;
    await h.nodes.get('chat-approval-mode').click(); await settle();
    assert.equal(h.nodes.get('chat-permissions-menu').hidden, false);
    assert.equal(h.modes.auto_approve.disabled, true);
    assert.equal(h.modes.auto_approve.getAttribute('aria-disabled'), 'true');
    assert.equal(h.nodes.get('chat-auto-approve-enable').disabled, true);
    assert.equal(h.modes.always_ask.disabled, false);
    assert.equal(h.modes.yolo.disabled, false);
    assert.equal(h.nodes.get('chat-auto-approve-setup').hidden, false);
    assert.ok(h.nodes.get('chat-auto-approve-unavailable').textContent);
    await h.modes.auto_approve.dispatchEvent({ type: 'click' });
    assert.equal(h.nodes.get('chat-auto-approve-dialog').open, false);
    assert.equal(h.writes().length, 0);
    await h.nodes.get('chat-auto-approve-setup').click(); await settle();
    assert.equal(h.nodes.get('chat-automation-dialog').open, true);
  });
}

test('a configured local reviewer enables explicit activation without a client-side key', async () => {
  const h = fixture({ availability: ready }); await h.initialized;
  await h.nodes.get('chat-approval-mode').click(); await settle();
  assert.equal(h.modes.auto_approve.disabled, false);
  assert.equal(h.nodes.get('chat-auto-approve-setup').hidden, true);
  await h.nodes.get('chat-permissions-menu').dispatchEvent({ type: 'keydown', key: 'End' });
  assert.equal(h.document.activeElement, h.modes.yolo);
  await h.modes.auto_approve.click();
  assert.equal(h.nodes.get('chat-auto-approve-dialog').open, true);
  assert.equal(h.nodes.get('chat-auto-approve-enable').disabled, false);
  await h.nodes.get('chat-auto-approve-enable').click();
  assert.equal(h.state.mode, 'auto_approve');
  assert.equal(h.nodes.get('chat-auto-approve-dialog').open, false);
  assert.equal(h.nodes.get('chat-permissions-label').textContent, 'Auto-Approve');
  assert.deepEqual(h.writes().map(request => request.body.changes), [{ approval_mode: 'auto_approve' }]);
});

test('saved unavailable Auto remains explicit and keyboard recovery preserves the disabled option', async () => {
  const h = fixture({ mode: 'auto_approve', availability: noKey }); await h.initialized;
  assert.equal(h.nodes.get('chat-permissions-label').textContent, 'Auto-Approve unavailable');
  assert.equal(h.window.ChatAutomation.getPolicy().approval_mode, 'auto_approve');
  await h.nodes.get('chat-approval-mode').click(); await settle();
  assert.equal(h.document.activeElement, h.modes.always_ask);
  await h.nodes.get('chat-permissions-menu').dispatchEvent({ type: 'keydown', key: 'ArrowDown' });
  assert.equal(h.document.activeElement, h.modes.yolo);
  await h.modes.always_ask.click();
  assert.equal(h.state.mode, 'always_ask');
  assert.equal(h.modes.auto_approve.disabled, true);
  assert.equal(h.modes.always_ask.disabled, false);
});

test('endpoint removal disables an open activation immediately and does not write approval mode', async () => {
  const h = fixture({ availability: ready }); await h.initialized;
  await h.modes.auto_approve.click();
  h.state.availability = noKey;
  const gate = deferred(); h.state.policyGate = gate;
  const refresh = h.window.dispatchEvent({ type: 'ge:model-endpoints-updated' });
  assert.equal(h.nodes.get('chat-auto-approve-enable').disabled, true);
  await h.nodes.get('chat-auto-approve-enable').dispatchEvent({ type: 'click' });
  gate.resolve(); await refresh; await settle();
  assert.equal(h.nodes.get('chat-auto-approve-enable').disabled, true);
  assert.equal(h.nodes.get('chat-auto-approve-cancel').disabled, false);
  assert.equal(h.writes().length, 0);
});

test('a rejected activation refreshes readiness instead of re-enabling the confirmation button', async () => {
  const h = fixture({ availability: ready }); await h.initialized;
  await h.modes.auto_approve.click(); h.state.rejectActivation = true;
  await h.nodes.get('chat-auto-approve-enable').click();
  assert.equal(h.nodes.get('chat-auto-approve-dialog').open, true);
  assert.equal(h.nodes.get('chat-auto-approve-enable').disabled, true);
  assert.equal(h.modes.auto_approve.disabled, true);
  assert.equal(h.nodes.get('chat-auto-approve-feedback').textContent, noKey.reason);
  assert.equal(h.state.mode, 'always_ask');
});

test('an older availability response cannot re-enable Auto after endpoint removal', async () => {
  const h = fixture({ availability: ready }); await h.initialized;
  const gate = deferred(); h.state.policyGate = gate;
  const oldRefresh = h.window.dispatchEvent({ type: 'odysseus:automation-changed' });
  await settle();
  h.state.availability = noKey;
  await h.window.dispatchEvent({ type: 'ge:model-endpoints-updated' });
  gate.resolve(); await oldRefresh;
  assert.equal(h.modes.auto_approve.disabled, true);
  assert.equal(h.nodes.get('chat-auto-approve-unavailable').textContent, noKey.reason);
});

test('refresh failures disable Auto while preserving Always Ask recovery', async () => {
  const h = fixture({ availability: ready, mode: 'auto_approve' }); await h.initialized;
  h.state.failRefresh = true;
  await h.window.dispatchEvent({ type: 'ge:model-endpoints-updated' });
  assert.equal(h.modes.auto_approve.disabled, true);
  assert.equal(h.nodes.get('chat-approval-mode').disabled, false);
  await h.modes.always_ask.click();
  assert.equal(h.state.mode, 'always_ask');
});
