/* Read-only activity; capture remains an explicit, limited next-turn action. */
export function decisionSummary(item) {
    const confidence = Object.values(item.questions || {}).map(answer =>
        `${answer.choice}: ${Math.round(answer.confidence * 100)}% confidence`).join('; ');
    const known = value => Number.isInteger(value) && value >= 0 ? value : 'unknown';
    const usage = item.usage_provenance === 'not_applicable' ? 'no model call' : item.usage ? `${known(item.usage.input_tokens)} in / ${known(item.usage.output_tokens)} out` : 'usage unknown (held)';
    const provider = item.provider ? ` · ${item.provider.name || item.provider.protocol} / ${item.provider.distribution_source}${item.provider.model ? ` / ${item.provider.model}` : ''}${item.provider.reviewed_model && item.provider.reviewed_model !== item.provider.model ? ` / reported ${item.provider.reviewed_model}` : ''}` : '';
    const review = item.approval;
    const assessment = review
        ? ` · ${review.decision_source} · risk ${review.risk_level || 'unassessed'} · authorization ${review.user_authorization || 'unassessed'}${review.disposition ? ` · ${review.disposition}` : ''}${review.reason ? ` · ${review.reason}` : ''}${review.fallback_reason ? ` · fallback: ${review.fallback_reason}` : ''} · ${review.policy}`
        : item.purpose === 'approval' ? ' · assessment unavailable (legacy)' : '';
    return `${item.purpose} · ${item.status} · ${usage}${confidence ? ` · ${confidence}` : ''}${provider}${assessment}${item.failure_kind ? ` · ${item.failure_kind}` : ''}`;
}

export function compactDecisionSummary(item) {
    const review = item.approval;
    if (!review) return decisionSummary(item);
    const sources = {jev: 'Jev review', conversation_model: 'Conversation review',
        human: 'Your decision', yolo: 'YOLO', deterministic: 'Action check'};
    const source = sources[review.decision_source] || 'Action review';
    const assessment = review.assessed === false ? 'unassessed'
        : `risk ${review.risk_level || 'unassessed'} · authorization ${review.user_authorization || 'unassessed'}`;
    const known = value => Number.isInteger(value) && value >= 0 ? value : 'unknown';
    const usage = item.usage_provenance === 'not_applicable' ? 'no model call'
        : item.usage ? `${known(item.usage.input_tokens)} in / ${known(item.usage.output_tokens)} out` : 'usage unknown (held)';
    return `${source} · ${review.disposition || item.status} · ${assessment} · ${usage}${review.reason ? ` · ${review.reason}` : ''}`;
}

export function distributionSummary(questions) {
    return Object.entries(questions).map(([qid, answer]) =>
        `${qid}: ${Object.entries(answer.probabilities).map(([choice, value]) =>
            `${choice} ${Math.round(value * 100)}%`).join(', ')}`).join('; ');
}

export function decisionExportPath(sessionId, runId, capture = false) {
    return `/api/session/${encodeURIComponent(sessionId)}/decisions/${encodeURIComponent(runId)}/${capture ? 'capture' : 'export'}`;
}

function mount(container, sessionId, {pane, trigger, closeButton, refreshButton}) {
    let destroyed = false, timer = null, controller = null, generation = 0, loaded = false;
    const narrow = window.matchMedia('(max-width: 900px)');
    const base = `/api/session/${encodeURIComponent(sessionId)}`;
    const content = document.createElement('div');
    content.className = 'decision-activity';
    const explanation = document.createElement('p');
    explanation.textContent = 'Confidence is reported by the model. It does not verify task success. Unknown usage remains held.';
    const state = document.createElement('p');
    state.id = 'chat-decision-status';
    state.setAttribute('role', 'status');
    const list = document.createElement('ol');
    list.setAttribute('aria-label', 'Decision attempts');
    const rows = new Map();
    const handoffList = document.createElement('ul');
    handoffList.setAttribute('aria-label', 'Execution handoffs');
    const taskState = document.createElement('p');
    const captureButton = document.createElement('button');
    captureButton.type = 'button';
    captureButton.textContent = 'Capture next turn (redacted)';
    captureButton.title = 'Stores opaque choices and metadata for one turn for 24 hours. Content is omitted. Expires unused after 15 minutes.';
    const deleteButton = document.createElement('button');
    deleteButton.type = 'button';
    deleteButton.textContent = 'Delete captures';
    const captureControls = document.createElement('div');
    captureControls.className = 'decision-capture-controls';
    captureControls.append(captureButton, deleteButton);
    const tools = document.createElement('details');
    tools.className = 'decision-activity-tools';
    const toolsLabel = document.createElement('summary');
    toolsLabel.textContent = 'Details & capture';
    tools.append(toolsLabel, explanation, captureControls, taskState);
    content.append(state, list, handoffList, tools);
    container.replaceChildren(content);
    trigger.disabled = false;

    async function request(path, options = {}) {
        const response = await fetch(`${base}${path}`, {credentials: 'same-origin', cache: 'no-store', ...options});
        if (!response.ok) throw new Error('Decision activity unavailable');
        return response.json();
    }
    function link(label, runId, capture = false) {
        const anchor = document.createElement('a');
        anchor.textContent = label;
        anchor.href = decisionExportPath(sessionId, runId, capture);
        anchor.setAttribute('download', '');
        return anchor;
    }
    const receipts = new Map();
    const setText = (node, value) => { if (node.textContent !== value) node.textContent = value; };
    function render(items) {
        const retained = new Set(), focused = document.activeElement;
        let cursor = list.firstChild;
        for (const item of items) {
            const key = item.attempt_id || `${item.run_id}:${item.purpose}`;
            retained.add(key);
            let entry = rows.get(key);
            if (!entry) {
                const row = document.createElement('li');
                const disclosure = document.createElement('details');
                const heading = document.createElement('summary');
                const text = document.createElement('span');
                const model = document.createElement('small');
                heading.append(text, model);
                const body = document.createElement('div');
                body.className = 'decision-entry-body';
                const values = document.createElement('p');
                const probabilities = document.createElement('p');
                const capture = link('Download capture', item.run_id, true);
                body.append(values, probabilities, link('Export metadata', item.run_id), capture);
                disclosure.append(heading, body); row.append(disclosure);
                entry = {row, text, model, values, probabilities, capture};
                rows.set(key, entry);
            }
            const labels = {approval: 'Action review', approval_review: 'Action review', model_auto: 'Model choice',
                tools: 'Tool choice', effort: 'Effort', approach: 'Approach', followup: 'Follow-up'};
            const source = item.approval?.decision_source;
            const reviewer = source === 'jev' ? 'Jev' : source === 'conversation_model' ? 'Conversation model' : '';
            setText(entry.text, `${labels[item.purpose] || item.purpose.replaceAll('_', ' ')} · ${item.approval?.disposition || item.status}`);
            setText(entry.model, [reviewer, item.provider?.model].filter(Boolean).join(' · '));
            entry.model.hidden = !entry.model.textContent;
            setText(entry.values, decisionSummary(item));
            setText(entry.probabilities, distributionSummary(item.questions || {}));
            entry.probabilities.hidden = !entry.probabilities.textContent;
            entry.capture.hidden = !['complete', 'outcome_recorded'].includes(item.capture_state);
            if (entry.row !== cursor) list.insertBefore(entry.row, cursor);
            cursor = entry.row.nextSibling;
        }
        for (const [key, entry] of rows) {
            if (!retained.has(key)) {entry.row.remove(); rows.delete(key);}
        }
        if (list.contains(focused) && document.activeElement !== focused) focused.focus({preventScroll:true});
        setText(state, items.length ? '' : 'No decisions recorded for this chat.');
    }
    function renderHandoffs(items) {
        const retained = new Set();
        let cursor = handoffList.firstChild;
        for (const item of items) {
            const key = item.id || item.handoff_id || item.run_id;
            retained.add(key);
            let entry = receipts.get(key);
            if (!entry) {
                const row = document.createElement('li'), details = document.createElement('details');
                const heading = document.createElement('summary'), body = document.createElement('p');
                details.append(heading, body); row.append(details);
                entry = {row, heading, body}; receipts.set(key, entry);
            }
            setText(entry.heading, `${item.review_required ? 'Review required' : 'Run'} · ${item.stop_reason.replaceAll('_', ' ')} · ${item.completion_verified ? 'verified' : 'unverified'}`);
            setText(entry.body, `${item.action_ids.length} actions · ${item.check_ids.length} checks`);
            if (entry.row !== cursor) handoffList.insertBefore(entry.row, cursor);
            cursor = entry.row.nextSibling;
        }
        for (const [key, entry] of receipts) {
            if (!retained.has(key)) {entry.row.remove(); receipts.delete(key);}
        }
    }
    function stopPolling() {
        ++generation;
        clearTimeout(timer);
        controller?.abort();
        controller = null;
        refreshButton.disabled = false;
        container.setAttribute('aria-busy', 'false');
    }
    async function refresh({manual = false} = {}) {
        if (destroyed || !pane.open || document.hidden || controller) return;
        clearTimeout(timer);
        const ticket = generation, current = new AbortController();
        controller = current;
        if (manual) refreshButton.disabled = true;
        if (!loaded) {
            container.setAttribute('aria-busy', 'true');
            setText(state, 'Loading decisions…');
        }
        try {
            const [data, handoffs, automation] = await Promise.all([
                request('/decisions/live', {signal: current.signal}),
                request('/decision-handoffs', {signal: current.signal}),
                request('/automation', {signal: current.signal})
            ]);
            if (destroyed || ticket !== generation || !pane.open) return;
            render(data.items || []);
            renderHandoffs(handoffs.items || []);
            loaded = true;
            const task = automation.task || {}, prep = task.preparation, route = task.routing || {};
            const parts = [`Task: ${task.previous_status || 'no recorded task'}`];
            if (prep) parts.push(`Context: ${prep.returned_bytes || 0} bytes returned · ${prep.coverage || 'unknown'} coverage`);
            if (route.followup) parts.push(`Follow-up: ${route.followup.relation} (meaning only)`);
            if (route.approach?.readiness) parts.push(`Readiness: ${route.approach.readiness.choice}`);
            if (route.effort?.status) parts.push(`Effort: ${route.effort.choice || route.effort.status}${route.effort.reason ? ' · ' + route.effort.reason.replaceAll('_', ' ') : ''}`);
            for (const [name, value] of Object.entries(route.diagnostics || {})) parts.push(`${name.replaceAll('_', ' ')}: ${value.choice} (advice)`);
            setText(taskState, parts.join(' · '));
        } catch (error) {
            if (!destroyed && ticket === generation && error.name !== 'AbortError') setText(state, 'Updates unavailable. Reload to try again.');
        } finally {
            if (!destroyed && ticket === generation) {
                controller = null;
                refreshButton.disabled = false;
                container.setAttribute('aria-busy', 'false');
                if (pane.open && !document.hidden) timer = setTimeout(refresh, 5000);
            }
        }
    }
    function close(restore = true) {
        stopPolling();
        if (pane.open) pane.close();
        trigger.setAttribute('aria-expanded', 'false');
        if (restore && !destroyed) trigger.focus();
    }
    function open() {
        if (destroyed || pane.open) return;
        if (narrow.matches) pane.showModal(); else pane.show();
        trigger.setAttribute('aria-expanded', 'true');
        closeButton.focus();
        refresh();
    }
    const toggle = () => pane.open ? close() : open();
    const cancel = event => {event.preventDefault(); close();};
    const closed = () => {if (!pane.open) close(false);};
    const escape = event => {
        if (!pane.open || event.key !== 'Escape' || event.defaultPrevented) return;
        // A newer modal owns its Escape; desktop activity remains nonmodal.
        if ([...document.querySelectorAll('dialog:modal')].some(dialog => dialog !== pane)) return;
        event.preventDefault(); close();
    };
    const visibility = () => {if (document.hidden) stopPolling(); else refresh();};
    const resize = () => {
        if (!pane.open) return;
        const focused = document.activeElement;
        pane.close();
        if (narrow.matches) pane.showModal(); else pane.show();
        if (pane.contains(focused)) focused.focus();
    };
    const closeClick = () => close();
    trigger.addEventListener('click', toggle);
    closeButton.addEventListener('click', closeClick);
    const reload = () => refresh({manual:true});
    refreshButton.addEventListener('click', reload);
    pane.addEventListener('cancel', cancel);
    pane.addEventListener('close', closed);
    document.addEventListener('keydown', escape);
    document.addEventListener('visibilitychange', visibility);
    narrow.addEventListener('change', resize);
    captureButton.addEventListener('click', async () => {
        captureButton.disabled = true;
        try {
            await request('/decision-capture', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
                purposes: ['model_auto', 'approach', 'approach_routing', 'tools', 'tool_selection', 'followup', 'followup_routing',
                    'effort', 'execution_effort', 'completion_advice', 'safety_advice', 'approval', 'approval_review',
                    'browser', 'browser_action', 'readiness', 'diagnostics', 'turn_preparation'], required: false
            })});
            if (!destroyed) state.textContent = 'Redacted capture enabled for the next turn. Unused consent expires in 15 minutes; capture is retained for 24 hours.';
        } catch (_) {
            if (!destroyed) state.textContent = 'Capture could not be enabled.';
        } finally {
            if (!destroyed) captureButton.disabled = false;
        }
    });
    deleteButton.addEventListener('click', async () => {
        deleteButton.disabled = true;
        try {
            await request('/decision-capture', {method: 'DELETE'});
            if (!destroyed) state.textContent = 'Capture consent and stored captures deleted.';
        } catch (_) {
            if (!destroyed) state.textContent = 'Captures could not be deleted.';
        } finally {
            if (!destroyed) deleteButton.disabled = false;
        }
    });
    return {refresh, destroy() {
        destroyed = true;
        close(false);
        trigger.disabled = true;
        trigger.removeEventListener('click', toggle);
        closeButton.removeEventListener('click', closeClick);
        refreshButton.removeEventListener('click', reload);
        pane.removeEventListener('cancel', cancel);
        pane.removeEventListener('close', closed);
        document.removeEventListener('keydown', escape);
        document.removeEventListener('visibilitychange', visibility);
        narrow.removeEventListener('change', resize);
        content.remove();
    }};
}

if (typeof window !== 'undefined') window.DecisionActivity = Object.freeze({mount});
