/* Read-only polling and explicit per-next-turn redacted capture controls. */
(() => {
    'use strict';

    function mount(container, sessionId) {
        let destroyed = false;
        let timer = null;
        let controller = null;
        const base = `/api/session/${encodeURIComponent(sessionId)}`;
        const disclosure = document.createElement('details');
        disclosure.className = 'decision-activity';
        const summary = document.createElement('summary');
        summary.textContent = 'Decisions';
        const explanation = document.createElement('p');
        explanation.textContent = 'Confidence is reported by the model. It does not verify task success. Unknown usage remains held.';
        const state = document.createElement('p');
        state.setAttribute('role', 'status');
        const list = document.createElement('ol');
        const handoffList = document.createElement('ul');
        const taskState = document.createElement('p');
        const captureButton = document.createElement('button');
        captureButton.type = 'button';
        captureButton.textContent = 'Capture next turn (redacted)';
        captureButton.title = 'Stores opaque choices and metadata for one turn for 24 hours. Content is omitted. Expires unused after 15 minutes.';
        const deleteButton = document.createElement('button');
        deleteButton.type = 'button';
        deleteButton.textContent = 'Delete captures';
        disclosure.append(summary, explanation, captureButton, deleteButton, state, taskState, list, handoffList);
        container.append(disclosure);

        async function request(path, options = {}) {
            const response = await fetch(`${base}${path}`, {credentials: 'same-origin', cache: 'no-store', ...options});
            if (!response.ok) throw new Error('Decision activity unavailable');
            return response.json();
        }

        function link(label, path) {
            const anchor = document.createElement('a');
            anchor.textContent = label;
            anchor.href = `${base}${path}`;
            anchor.setAttribute('download', '');
            return anchor;
        }

        function render(items) {
            list.replaceChildren();
            for (const item of items) {
                const row = document.createElement('li');
                const text = document.createElement('span');
                const confidence = Object.values(item.questions || {}).map(answer =>
                    `${answer.choice}: ${Math.round(answer.confidence * 100)}% confidence`).join('; ');
                const usage = item.usage ? `${item.usage.input_tokens} in / ${item.usage.output_tokens} out` : 'usage unknown (held)';
                const provider = item.provider ? ` · ${item.provider.protocol} / ${item.provider.distribution_source}` : '';
                text.textContent = `${item.purpose} · ${item.status} · ${usage}${confidence ? ` · ${confidence}` : ''}${provider}${item.failure_kind ? ` · ${item.failure_kind}` : ''}`;
                row.append(text, document.createTextNode(' '), link('Export metadata', `/decisions/${encodeURIComponent(item.run_id)}/export`));
                if (Object.keys(item.questions || {}).length) {
                    const distribution = document.createElement('details');
                    const label = document.createElement('summary');
                    label.textContent = 'Reported distribution';
                    const values = document.createElement('p');
                    values.textContent = Object.entries(item.questions).map(([qid, answer]) =>
                        `${qid}: ${Object.entries(answer.probabilities).map(([choice, value]) =>
                            `${choice} ${Math.round(value * 100)}%`).join(', ')}`).join('; ');
                    distribution.append(label, values);
                    row.append(distribution);
                }
                if (['complete', 'outcome_recorded'].includes(item.capture_state)) {
                    row.append(document.createTextNode(' '), link('Download redacted capture', `/decisions/${encodeURIComponent(item.run_id)}/capture`));
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

        async function refresh() {
            if (destroyed || !disclosure.open || document.hidden) return;
            controller = new AbortController();
            try {
                const [data, handoffs, automation] = await Promise.all([
                    request('/decisions/live', {signal: controller.signal}),
                    request('/decision-handoffs', {signal: controller.signal}),
                    request('/automation', {signal: controller.signal})
                ]);
                if (!destroyed) {
                    render(data.items || []);
                    renderHandoffs(handoffs.items || []);
                    const task=automation.task||{}, prep=task.preparation, route=task.routing||{};
                    const parts=[`Task: ${task.previous_status||'no recorded task'}`];
                    if(prep)parts.push(`Context: ${prep.returned_bytes||0} bytes returned · ${prep.coverage||'unknown'} coverage`);
                    if(route.followup)parts.push(`Follow-up: ${route.followup.relation} (meaning only)`);
                    if(route.approach?.readiness)parts.push(`Readiness: ${route.approach.readiness.choice}`);
                    if(route.effort?.status)parts.push(`Effort: ${route.effort.choice||route.effort.status}${route.effort.reason?' · '+route.effort.reason.replaceAll('_',' '):''}`);
                    for(const [name,value] of Object.entries(route.diagnostics||{}))parts.push(`${name.replaceAll('_',' ')}: ${value.choice} (advice)`);
                    taskState.textContent=parts.join(' · ');
                }
            } catch (error) {
                if (!destroyed && error.name !== 'AbortError') state.textContent = 'Decision activity unavailable.';
            } finally {
                controller = null;
                if (!destroyed && disclosure.open) timer = setTimeout(refresh, 2500);
            }
        }

        disclosure.addEventListener('toggle', () => {
            clearTimeout(timer);
            controller?.abort();
            if (disclosure.open) refresh();
        });
        captureButton.addEventListener('click', async () => {
            captureButton.disabled = true;
            try {
                await request('/decision-capture', {method: 'PUT', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({
                    purposes: ['model_auto', 'approach', 'approach_routing', 'tools', 'tool_selection', 'followup', 'followup_routing',
                        'effort', 'execution_effort', 'completion_advice', 'safety_advice', 'approval', 'approval_review',
                        'browser', 'browser_action', 'readiness', 'diagnostics', 'turn_preparation'], required: false
                })});
                state.textContent = 'Redacted capture enabled for the next turn. Unused consent expires in 15 minutes; capture is retained for 24 hours.';
            } catch (_) {
                state.textContent = 'Capture could not be enabled.';
            } finally {
                if (!destroyed) captureButton.disabled = false;
            }
        });
        deleteButton.addEventListener('click', async () => {
            try {
                await request('/decision-capture', {method: 'DELETE'});
                state.textContent = 'Capture consent and stored captures deleted.';
            } catch (_) {
                state.textContent = 'Captures could not be deleted.';
            }
        });
        return {refresh, destroy() {
            destroyed = true;
            clearTimeout(timer);
            controller?.abort();
            disclosure.remove();
        }};
    }

    window.DecisionActivity = Object.freeze({mount});
})();
