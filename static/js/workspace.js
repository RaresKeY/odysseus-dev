// Workspace choices are browser preferences; every selection is vetted by the
// server and tool execution independently seals the chosen resource root.
import Storage, { KEYS } from './storage.js';
import { makeWindowDraggable } from './windowDrag.js';

const API_BASE = window.location.origin;
const _FOLDER_SVG = '<svg class="workspace-row-icon" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg>';
const _MAX_SAVED = 20;
let _modal = null;
let _defaultWorkspaceLoad = null;
let _selectionRevision = 0;
let _browseRevision = 0;
let _returnFocus = null;

export function getWorkspace() {
  const value = Storage.get(KEYS.WORKSPACE, '');
  return typeof value === 'string' ? value : '';
}

export function getSavedWorkspaces() {
  const stored = Storage.getJSON(KEYS.SAVED_WORKSPACES, []);
  if (!Array.isArray(stored)) return [];
  return [...new Set(stored.filter(path => typeof path === 'string' && path.trim()))].slice(0, _MAX_SAVED);
}

function _remember(path) {
  Storage.setJSON(KEYS.SAVED_WORKSPACES, [path, ...getSavedWorkspaces().filter(item => item !== path)].slice(0, _MAX_SAVED));
}

function _basename(path) {
  if (!path) return '';
  const parts = path.replace(/[\\/]+$/, '').split(/[\\/]/);
  return parts[parts.length - 1] || path;
}

function _isChatMode() {
  return document.getElementById('mode-chat-btn')?.classList.contains('active');
}

export function syncWorkspaceIndicator(path) {
  const chat = _isChatMode();
  const bar = document.getElementById('chat-workspace-bar');
  const pill = document.getElementById('workspace-indicator-btn');
  const name = document.getElementById('workspace-indicator-name');
  const overflow = document.getElementById('overflow-workspace-btn');
  if (bar) bar.hidden = !path || chat;
  if (pill) {
    pill.title = path || '';
    pill.setAttribute('aria-label', path ? `Change workspace: ${path}` : 'Select workspace');
  }
  if (name) name.textContent = _basename(path);
  if (overflow) {
    overflow.style.display = chat ? 'none' : '';
    overflow.classList.toggle('active', !!path);
  }
  _renderSaved();
  document.dispatchEvent(new CustomEvent('overflow-state-change'));
}

export function applyMode(_mode) {
  syncWorkspaceIndicator(getWorkspace());
}

export function setWorkspace(path) {
  ++_selectionRevision;
  // An explicit empty preference survives reload and prevents first-launch
  // default discovery from undoing the user's Leave action.
  Storage.set(KEYS.WORKSPACE, path || '');
  if (path) _remember(path);
  syncWorkspaceIndicator(path || '');
}

export async function vetAndSetWorkspace(path) {
  const revision = ++_selectionRevision;
  try {
    const res = await fetch(`${API_BASE}/api/workspace/vet?path=${encodeURIComponent(path)}`, { credentials: 'same-origin' });
    if (!res.ok) return { ok: false, path: null };
    const data = await res.json();
    if (revision !== _selectionRevision) return { ok: false, path: null, superseded: true };
    if (data.ok && data.path) {
      setWorkspace(data.path);
      return { ok: true, path: data.path };
    }
  } catch (_) {}
  return { ok: false, path: null };
}

export async function loadDefaultWorkspace() {
  if (_defaultWorkspaceLoad) return _defaultWorkspaceLoad;
  const revision = _selectionRevision;
  _defaultWorkspaceLoad = fetch(`${API_BASE}/api/workspace/default`, { credentials: 'same-origin' })
    .then(async (res) => {
      if (!res.ok) return { ok: false, path: null };
      const data = await res.json();
      if (data.ok && data.path && revision === _selectionRevision && Storage.get(KEYS.WORKSPACE, null) === null) {
        setWorkspace(data.path);
        return { ok: true, path: data.path };
      }
      return { ok: false, path: null };
    })
    .catch(() => ({ ok: false, path: null }));
  return _defaultWorkspaceLoad;
}

export function clearWorkspace() {
  setWorkspace('');
  const target = _modal?.style.display !== 'none' && _modal
    ? _modal.querySelector('#workspace-cur-path') : document.getElementById('message');
  target?.focus();
}

function _feedback(text) {
  const feedback = _modal?.querySelector('#workspace-feedback');
  if (feedback) feedback.textContent = text;
}

function _pathButton(path, onClick) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'workspace-row workspace-choice';
  button.title = path;
  button.innerHTML = _FOLDER_SVG;
  const label = document.createElement('span');
  label.className = 'workspace-path-label';
  const name = document.createElement('span');
  name.textContent = _basename(path);
  const fullPath = document.createElement('small');
  fullPath.textContent = path;
  label.append(name, fullPath);
  button.append(label);
  button.addEventListener('click', onClick);
  return button;
}

function _closeButton(label, onClick) {
  const button = document.createElement('button');
  button.type = 'button';
  button.className = 'workspace-remove';
  button.setAttribute('aria-label', label);
  button.title = label;
  button.innerHTML = '<svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m6 6 12 12M6 18 18 6"/></svg>';
  button.addEventListener('click', onClick);
  return button;
}

function _renderSaved() {
  if (!_modal) return;
  const current = getWorkspace();
  const selected = _modal.querySelector('#workspace-selected');
  selected.replaceChildren();
  selected.hidden = !current;
  if (current) {
    const heading = document.createElement('h5');
    heading.textContent = 'Selected workspace';
    const row = document.createElement('div');
    row.className = 'workspace-saved-row workspace-selected-row';
    row.append(_pathButton(current, () => _navigate(current)), _closeButton('Leave workspace', clearWorkspace));
    selected.append(heading, row);
  }
  const list = _modal.querySelector('#workspace-saved-list');
  list.replaceChildren();
  const paths = getSavedWorkspaces().filter(path => path !== current);
  if (!paths.length) {
    const empty = document.createElement('p');
    empty.className = 'workspace-empty';
    empty.textContent = current ? 'Other saved folders appear here.' : 'Choose a folder to save it here.';
    list.append(empty);
  }
  for (const path of paths) {
    const row = document.createElement('div');
    row.className = 'workspace-saved-row';
    row.append(_pathButton(path, () => _choose(path)), _closeButton(`Remove saved workspace: ${path}`, () => {
      Storage.setJSON(KEYS.SAVED_WORKSPACES, getSavedWorkspaces().filter(item => item !== path));
      _renderSaved();
      _modal.querySelector('#workspace-cur-path').focus();
    }));
    list.append(row);
  }
}

async function _choose(path) {
  if (!path.trim()) return;
  const button = _modal.querySelector('#workspace-use');
  button.disabled = true;
  const browseRevision = ++_browseRevision;
  _feedback('Checking folder…');
  const result = await vetAndSetWorkspace(path);
  // Closing the picker or starting another navigation must not update an old
  // dialog request's controls, even if that request completes later.
  if (browseRevision !== _browseRevision) return;
  button.disabled = false;
  if (result.ok) {
    closeWorkspaceBrowser();
  } else if (result.superseded) {
    _feedback('');
  } else {
    _feedback('This folder is unavailable or cannot be used. Choose another folder or remove it from saved workspaces.');
  }
}

async function _load(path) {
  const url = `${API_BASE}/api/workspace/browse${path ? `?path=${encodeURIComponent(path)}` : ''}`;
  const res = await fetch(url, { credentials: 'same-origin' });
  if (!res.ok) throw new Error(`browse failed: ${res.status}`);
  return res.json();
}

function _render(data) {
  const body = _modal.querySelector('#workspace-body');
  const pathEl = _modal.querySelector('#workspace-cur-path');
  pathEl.value = data.path;
  pathEl.title = data.path;
  body.replaceChildren();
  function addFolder(path, label) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'workspace-row workspace-directory';
    button.innerHTML = _FOLDER_SVG;
    const text = document.createElement('span');
    text.textContent = label;
    button.append(text);
    button.addEventListener('click', () => _navigate(path));
    body.append(button);
  }
  if (data.parent) addFolder(data.parent, '..');
  for (const directory of data.dirs) addFolder(directory.path, directory.name);
  if (data.truncated || !body.children.length) {
    const empty = document.createElement('p');
    empty.className = 'workspace-empty';
    empty.textContent = data.truncated ? 'More folders available. Enter a path above.' : 'No subfolders';
    body.append(empty);
  }
  const useBtn = _modal.querySelector('#workspace-use');
  useBtn.disabled = data.selectable === false;
  useBtn.title = useBtn.disabled ? 'This folder cannot be used as a workspace' : '';
}

async function _navigate(path) {
  ++_selectionRevision;
  const revision = ++_browseRevision;
  _feedback('Loading folder…');
  _modal.querySelector('#workspace-use').disabled = true;
  try {
    const data = await _load(path);
    if (revision !== _browseRevision) return;
    _render(data);
    _feedback('');
  } catch (_) {
    if (revision !== _browseRevision) return;
    _feedback('Could not open folder. Select a saved workspace or enter another path.');
  }
}

function _getModal() {
  if (_modal) return _modal;
  _modal = document.createElement('div');
  _modal.id = 'workspace-modal';
  _modal.className = 'modal';
  // A picker is temporary. The active workspace lives in the composer chip,
  // rather than keeping this dialog alive as a minimized tool tab.
  _modal.dataset.noMinimize = 'true';
  _modal.style.display = 'none';
  _modal.innerHTML = `
    <div class="modal-content" role="dialog" aria-modal="true" aria-labelledby="workspace-title">
      <div class="modal-header">
        <h4 id="workspace-title">${_FOLDER_SVG}Select workspace</h4>
        <button type="button" class="close-btn" id="workspace-close" aria-label="Close workspace picker">×</button>
      </div>
      <div class="workspace-scroll quiet-scroll">
        <section class="workspace-choices" id="workspace-selected" hidden></section>
        <section class="workspace-choices">
          <h5>Saved workspaces</h5>
          <div id="workspace-saved-list"></div>
        </section>
        <label class="workspace-browse-label" for="workspace-cur-path">Browse folders</label>
        <input type="text" class="styled-prompt-input workspace-cur" id="workspace-cur-path"
               spellcheck="false" autocomplete="off" autocapitalize="off" autocorrect="off"
               placeholder="Enter a folder path" />
        <p class="muted workspace-note">File tools stay in this folder. Shell commands start here and can reach outside it.</p>
        <div class="workspace-body" id="workspace-body"></div>
      </div>
      <p id="workspace-feedback" class="muted workspace-note" role="status" aria-live="polite"></p>
      <div class="modal-footer workspace-footer">
        <button type="button" class="confirm-btn confirm-btn-secondary" id="workspace-cancel">Cancel</button>
        <button type="button" class="confirm-btn confirm-btn-primary" id="workspace-use">${_FOLDER_SVG}Use this folder</button>
      </div>
    </div>`;
  document.body.appendChild(_modal);
  _modal.querySelector('#workspace-close').addEventListener('click', closeWorkspaceBrowser);
  _modal.querySelector('#workspace-cancel').addEventListener('click', closeWorkspaceBrowser);
  document.addEventListener('keydown', (event) => {
    if (event.key !== 'Escape' || _modal.style.display === 'none') return;
    event.preventDefault();
    event.stopImmediatePropagation();
    closeWorkspaceBrowser();
  }, true);
  const pathEl = _modal.querySelector('#workspace-cur-path');
  pathEl.addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
      event.preventDefault();
      if (pathEl.value.trim()) _navigate(pathEl.value.trim());
    }
  });
  pathEl.addEventListener('input', () => {
    ++_selectionRevision;
    ++_browseRevision;
    _modal.querySelector('#workspace-use').disabled = !pathEl.value.trim();
    _feedback('');
  });
  _modal.querySelector('#workspace-use').addEventListener('click', () => _choose(pathEl.value.trim()));
  makeWindowDraggable(_modal, {
    content: _modal.querySelector('.modal-content'),
    header: _modal.querySelector('.modal-header'),
    enableDock: false,
  });
  return _modal;
}

export async function openWorkspaceBrowser() {
  const modal = _getModal();
  _returnFocus = document.activeElement;
  _renderSaved();
  modal.style.display = 'flex';
  await _navigate(getWorkspace() || '');
  if (modal.style.display !== 'none') modal.querySelector('#workspace-cur-path').focus();
}

export function closeWorkspaceBrowser() {
  ++_browseRevision;
  // Also invalidate an unfinished selection when the user cancels.
  ++_selectionRevision;
  if (_modal) _modal.style.display = 'none';
  const target = [_returnFocus, document.getElementById('workspace-indicator-btn'), document.getElementById('message')]
    .find(element => element?.isConnected && element.getClientRects().length);
  target?.focus();
}

export function initWorkspace() {
  const current = getWorkspace();
  if (current) _remember(current);
  syncWorkspaceIndicator(current);
  if (Storage.get(KEYS.WORKSPACE, null) === null) loadDefaultWorkspace();
  document.getElementById('overflow-workspace-btn')?.addEventListener('click', openWorkspaceBrowser);
  document.getElementById('workspace-indicator-btn')?.addEventListener('click', openWorkspaceBrowser);
  document.getElementById('workspace-clear-btn')?.addEventListener('click', clearWorkspace);
}

export default { initWorkspace, openWorkspaceBrowser, getWorkspace, getSavedWorkspaces, setWorkspace, vetAndSetWorkspace, loadDefaultWorkspace, clearWorkspace, syncWorkspaceIndicator, applyMode };
