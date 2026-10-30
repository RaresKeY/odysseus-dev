// Auto uses the same registered catalogue as the composer, in a native dialog.
export const AUTO_CATEGORIES = [
  ['complex_tasks', 'Complex tasks'],
  ['straightforward_tasks', 'Straightforward tasks'],
];
export const modelIdentityKey = model => JSON.stringify([model?.endpoint_id, model?.model]);
export const catalogueIdentity = model => ({endpoint_id: model.endpointId, model: model.mid});
export const eligibleAutoModel = model => !!model?.endpointId && !model.stale && model.modelType === 'llm';

export function findCategoryModel(identity, models) {
  return models.find(model => modelIdentityKey(catalogueIdentity(model)) === modelIdentityKey(identity));
}
export function categoriesAvailable(categories, models) {
  return AUTO_CATEGORIES.every(([id]) => eligibleAutoModel(findCategoryModel(categories?.[id], models)));
}
export function filterAutoModels(models, query) {
  const words = query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return models.filter(model => model.endpointId && model.modelType === 'llm'
    && words.every(word => [model.mid, model.display, model.epName, model.endpointId, model.providerText]
      .join(' ').toLocaleLowerCase().includes(word)));
}

export function createAutoCategoryDialog({getModels, refreshModels, returnFocus, onSaved}) {
  const dialog = document.getElementById('model-auto-dialog');
  const search = document.getElementById('model-auto-search');
  const list = document.getElementById('model-auto-models');
  const feedback = document.getElementById('model-auto-dialog-feedback');
  const save = document.getElementById('model-auto-save');
  const refresh = document.getElementById('model-auto-refresh');
  const tabs = AUTO_CATEGORIES.map(([id]) => document.getElementById(`model-auto-${id}`));
  let draft = {}, context = null, category = 'complex_tasks', saving = false, loading = false;
  let generation = 0, loadError = false, queries = {}, scrolls = {};
  const copyAssignments = categories => Object.fromEntries(AUTO_CATEGORIES
    .filter(([id]) => categories?.[id])
    .map(([id]) => [id, {endpoint_id: categories[id].endpoint_id, model: categories[id].model}]));
  function selectedText(id) {
    const identity = draft[id];
    if (!identity) return 'Choose a model';
    const model = findCategoryModel(identity, getModels());
    return `${identity.model} · ${model?.epName || identity.endpoint_id}${eligibleAutoModel(model) ? '' : ' · unavailable'}`;
  }
  function renderSelection() {
    tabs.forEach((tab, index) => {
      const id = AUTO_CATEGORIES[index][0], active = category === id;
      tab.setAttribute('aria-selected', String(active)); tab.tabIndex = active ? 0 : -1;
      tab.disabled = saving;
      tab.querySelector('small').textContent = selectedText(id);
      tab.querySelector('small').title = selectedText(id);
    });
    document.getElementById('model-auto-panel').setAttribute('aria-labelledby', `model-auto-${category}`);
    const model = findCategoryModel(draft[category], getModels());
    const efforts = model?.autoCapabilities?.supported_efforts || ['default'];
    document.getElementById('model-auto-selected').textContent = draft[category]
      ? `${selectedText(category)} — ${efforts.length === 1 && efforts[0] === 'default'
        ? 'Model default; explicit effort unavailable' : `Jev chooses supported effort: ${efforts.join(', ')}`}`
      : 'Select one registered API model for this category.';
    document.getElementById('model-auto-assigned').textContent = `${AUTO_CATEGORIES.filter(([id]) => draft[id]).length}/2 assigned`;
    save.disabled = saving || loading || loadError || !categoriesAvailable(draft, getModels());
    save.textContent = saving ? 'Saving…' : 'Save & enable Auto';
    refresh.disabled = saving || loading;
    search.disabled = saving;
    for (const id of ['model-auto-cancel', 'model-auto-close']) document.getElementById(id).disabled = saving;
    list.querySelectorAll('[data-model-key]').forEach(row => {
      const selected = row.dataset.modelKey === modelIdentityKey(draft[category]);
      row.setAttribute('aria-selected', String(selected));
      row.classList.toggle('model-auto-selected', selected);
      row.querySelector('.model-auto-radio').textContent = selected ? 'Selected' : '';
      row.disabled = saving || row.dataset.unavailable === 'true';
    });
  }
  function renderModels() {
    const focusedKey = list.contains(document.activeElement) ? document.activeElement.dataset.modelKey : null;
    list.replaceChildren();
    const matching = filterAutoModels(getModels(), search.value);
    const models = matching.slice(0, 250);
    list.setAttribute('aria-busy', String(loading));
    document.getElementById('model-auto-results').textContent = loading ? 'Refreshing models…'
      : `${matching.length} ${matching.length === 1 ? 'model' : 'models'}${matching.length > 250 ? ' · showing first 250; search to narrow' : ''}`;
    if (!models.length) {
      const empty = document.createElement('p'); empty.className = 'model-switch-empty';
      empty.textContent = loading ? 'Loading registered models…' : loadError ? 'Models could not be loaded. Refresh to try again.'
        : search.value.trim() ? 'No matching models. Try another model or provider name.'
          : 'No registered API models available. Add an endpoint in Settings, then refresh.';
      list.append(empty);
    }
    models.forEach(model => {
      const row = document.createElement('button'); row.type = 'button';
      row.className = 'model-switch-item'; row.setAttribute('role', 'option'); row.tabIndex = -1;
      row.dataset.modelKey = modelIdentityKey(catalogueIdentity(model));
      row.dataset.unavailable = String(!eligibleAutoModel(model));
      const text = document.createElement('span'); text.className = 'model-auto-model-label';
      const name = document.createElement('strong'); name.textContent = model.mid;
      const endpoint = document.createElement('small');
      endpoint.textContent = `${model.epName || 'API'} · ${model.endpointId}${model.stale ? ' · unavailable' : ''}`;
      text.append(name, endpoint);
      const radio = document.createElement('span'); radio.className = 'model-auto-radio';
      row.append(text, radio); row.title = `${model.mid} · ${endpoint.textContent}`;
      row.addEventListener('click', () => {
        if (saving || !eligibleAutoModel(model)) return;
        draft[category] = catalogueIdentity(model); feedback.textContent = ''; renderSelection();
      });
      list.append(row);
    });
    renderSelection();
    if (focusedKey) [...list.querySelectorAll('[data-model-key]')]
      .find(row => row.dataset.modelKey === focusedKey)?.focus({preventScroll: true});
  }
  async function load() {
    const ticket = ++generation; loading = true; loadError = false;
    if (feedback.textContent.startsWith('Models could not be loaded')) feedback.textContent = '';
    renderModels();
    try { await refreshModels(); }
    catch (_) { if (ticket === generation) { loadError = true; feedback.textContent = 'Models could not be loaded. Refresh to try again.'; } }
    finally { if (ticket === generation && dialog.open) { loading = false; renderModels(); } }
  }
  function close() { if (!saving) dialog.close(); }
  function open() {
    if (dialog.open || !window.ChatAutomation) return;
    draft = copyAssignments(window.ChatAutomation.getPolicy()?.auto_categories);
    context = window.ChatAutomation.selectionContext(); category = 'complex_tasks'; queries = {}; scrolls = {};
    feedback.textContent = ''; search.value = ''; loadError = false;
    const policy = window.ChatAutomation.getPolicy();
    document.getElementById('model-auto-migration').textContent = policy?.auto_models?.length && !Object.keys(draft).length
      ? 'Your previous model choices are preserved. Assign a model to each category to use Auto.' : '';
    dialog.showModal(); renderModels(); search.focus(); load();
  }
  tabs.forEach((tab, index) => {
    function activate() {
      if (saving) return;
      queries[category] = search.value; scrolls[category] = list.scrollTop;
      category = AUTO_CATEGORIES[index][0]; search.value = queries[category] || '';
      renderModels(); list.scrollTop = scrolls[category] || 0;
    }
    tab.addEventListener('click', activate);
    tab.addEventListener('keydown', event => {
      if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
      event.preventDefault();
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? 1 : 1 - index;
      tabs[next].click(); tabs[next].focus();
    });
  });
  search.addEventListener('input', () => { queries[category] = search.value; renderModels(); list.scrollTop = 0; });
  function navigate(event) {
    if (!['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) return;
    const rows = [...list.querySelectorAll('[role=option]:not(:disabled)')]; if (!rows.length) return;
    if (event.target === search && !['ArrowDown', 'ArrowUp'].includes(event.key)) return;
    event.preventDefault(); const index = rows.indexOf(document.activeElement);
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? rows.length - 1
      : index < 0 ? event.key === 'ArrowDown' ? 0 : rows.length - 1
        : (index + (event.key === 'ArrowDown' ? 1 : -1) + rows.length) % rows.length;
    rows[next].focus();
  }
  search.addEventListener('keydown', navigate); list.addEventListener('keydown', navigate);
  refresh.addEventListener('click', load);
  for (const id of ['model-auto-close', 'model-auto-cancel']) document.getElementById(id).addEventListener('click', close);
  dialog.addEventListener('cancel', event => { if (saving) event.preventDefault(); });
  dialog.addEventListener('close', () => {
    if (dialog.open) return; // A queued close event can belong to an earlier opening.
    ++generation; saving = false; loading = false; draft = {}; context = null; returnFocus();
  });
  save.addEventListener('click', async () => {
    if (save.disabled) return;
    const saveGeneration = generation;
    saving = true; renderSelection(); feedback.textContent = 'Saving categories…';
    try {
      await window.ChatAutomation.saveAutoCategories(copyAssignments(draft), context);
      if (generation !== saveGeneration || !dialog.open) return;
      saving = false; dialog.close(); onSaved();
    } catch (error) {
      if (generation === saveGeneration && dialog.open) feedback.textContent = error.message;
    } finally {
      if (generation === saveGeneration && dialog.open) { saving = false; renderSelection(); }
    }
  });
  window.addEventListener('odysseus:session-changed', event => {
    if (event.detail?.materialized && saving) return;
    if (dialog.open) { ++generation; saving = false; dialog.close(); }
  });
  return {open};
}
