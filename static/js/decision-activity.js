/* Read-only activity; capture remains an explicit, limited next-turn action. */
export function decisionSummary(item) {
    const confidence = Object.values(item.questions || {}).map(answer =>
        `${answer.choice}: ${Math.round(answer.confidence * 100)}% confidence`).join('; ');
    const known = value => Number.isInteger(value) && value >= 0 ? value : 'unknown';
    const usage = item.usage ? `${known(item.usage.input_tokens)} in / ${known(item.usage.output_tokens)} out` : 'usage unknown (held)';
    const provider = item.provider ? ` · ${item.provider.protocol} / ${item.provider.distribution_source}` : '';
    return `${item.purpose} · ${item.status} · ${usage}${confidence ? ` · ${confidence}` : ''}${provider}${item.failure_kind ? ` · ${item.failure_kind}` : ''}`;
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
    let destroyed = false, timer = null, controller = null, generation = 0;
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
    content.append(explanation, captureControls, state, taskState, list, handoffList);
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
    function render(items) {
        list.replaceChildren();
        for (const item of items) {
            const row = document.createElement('li');
            const text = document.createElement('span');
            text.textContent = decisionSummary(item);
            row.append(text, document.createTextNode(' '), link('Export metadata', item.run_id));
            if (Object.keys(item.questions || {}).length) {
                const distribution = document.createElement('details');
                const label = document.createElement('summary');
                label.textContent = 'Reported distribution';
                const values = document.createElement('p');
                values.textContent = distributionSummary(item.questions);
                distribution.append(label, values);
                row.append(distribution);
            }
            if (['complete', 'outcome_recorded'].includes(item.capture_state)) {
                row.append(document.createTextNode(' '), link('Download redacted capture', item.run_id, true));
            }
            list.append(row);
        }
        state.textContent = items.length ? '' : 'No recorded decision attempts.';
    }
    function renderHandoffs(items) {
        handoffList.replaceChildren();
        for (const item of items) {
            const row = document.createElement('li');
            const verdict = item.completion_verified ? 'completion verified by execution evidence' : 'completion unverified';
            row.textContent = `${item.review_required ? 'Review required' : 'Run receipt'} · ${item.stop_reason} · ${verdict}`
                + ` · ${item.action_ids.length} action receipts / ${item.check_ids.length} check receipts`;
            handoffList.append(row);
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
    async function refresh() {
        if (destroyed || !pane.open || document.hidden) return;
        stopPolling();
        const ticket = generation, current = new AbortController();
        controller = current;
        refreshButton.disabled = true;
        container.setAttribute('aria-busy', 'true');
        state.textContent = 'Loading decision activity…';
        try {
            const [data, handoffs, automation] = await Promise.all([
                request('/decisions/live', {signal: current.signal}),
                request('/decision-handoffs', {signal: current.signal}),
                request('/automation', {signal: current.signal})
            ]);
            if (destroyed || ticket !== generation || !pane.open) return;
            render(data.items || []);
            renderHandoffs(handoffs.items || []);
            const task = automation.task || {}, prep = task.preparation, route = task.routing || {};
            const parts = [`Task: ${task.previous_status || 'no recorded task'}`];
            if (prep) parts.push(`Context: ${prep.returned_bytes || 0} bytes returned · ${prep.coverage || 'unknown'} coverage`);
            if (route.followup) parts.push(`Follow-up: ${route.followup.relation} (meaning only)`);
            if (route.approach?.readiness) parts.push(`Readiness: ${route.approach.readiness.choice}`);
            if (route.effort?.status) parts.push(`Effort: ${route.effort.choice || route.effort.status}${route.effort.reason ? ' · ' + route.effort.reason.replaceAll('_', ' ') : ''}`);
            for (const [name, value] of Object.entries(route.diagnostics || {})) parts.push(`${name.replaceAll('_', ' ')}: ${value.choice} (advice)`);
            taskState.textContent = parts.join(' · ');
        } catch (error) {
            if (!destroyed && ticket === generation && error.name !== 'AbortError') state.textContent = 'Decision activity unavailable. Refresh to try again.';
        } finally {
            if (!destroyed && ticket === generation) {
                controller = null;
                refreshButton.disabled = false;
                container.setAttribute('aria-busy', 'false');
                if (pane.open && !document.hidden) timer = setTimeout(refresh, 2500);
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
    const escape = event => {if (pane.open && event.key === 'Escape') {event.preventDefault(); close();}};
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
    refreshButton.addEventListener('click', refresh);
    pane.addEventListener('cancel', cancel);
    pane.addEventListener('close', closed);
    pane.addEventListener('keydown', escape);
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
        refreshButton.removeEventListener('click', refresh);
        pane.removeEventListener('cancel', cancel);
        pane.removeEventListener('close', closed);
        pane.removeEventListener('keydown', escape);
        document.removeEventListener('visibilitychange', visibility);
        narrow.removeEventListener('change', resize);
        content.remove();
    }};
}

if (typeof window !== 'undefined') window.DecisionActivity = Object.freeze({mount});
