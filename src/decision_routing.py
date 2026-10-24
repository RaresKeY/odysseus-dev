"""Bound, permission-first decision preparation; selections never grant authority.

The complete frozen bundle/union encoding and intent-only follow-up separation
adapt the corrected Odysseus Jev concern-series design. No donor runtime is used.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from src.decisions.contracts import DecisionContext, DecisionError, DecisionRequest, encode

THRESHOLD = .80
RELATIONS = {
    'continuation': 'Continue or steer the previous task.',
    'confirmation': 'An intent confirmation linked to a pending server card; never approval.',
    'new_topic': 'A distinct task or topic.',
    'casual': 'A social exchange with no task continuation.',
    'cancel': 'Intent to stop earlier work; grants no execution permission.',
    'unknown': 'Insufficient evidence to establish a relation.',
}
DIAGNOSTICS = {
    'online_search': 'Would external facts or documentation benefit the immediate deliverable?',
    'fresh_information': 'Does the immediate deliverable require current external facts?',
    'project_context': 'Would a relevant maintained project summary or focused specs benefit this turn?',
}
YES_NO = {'yes': 'Relevant now.', 'no': 'Not needed for this turn.', 'unclear': 'Insufficient evidence.'}


def _hash(value):
    return hashlib.sha256(encode(value)).hexdigest()


def decision_context(bound):
    return DecisionContext(bound.owner, bound.session_id, bound.run_id,
        policy_revision=bound.policy['policy_revision'],
        authority_epoch=bound.policy['authority_epoch'], cancelled=lambda: not bound.current())


def _accepted(answer):
    return (isinstance(answer, dict) and answer.get('confidence', 0) >= THRESHOLD
            and answer.get('probabilities', {}).get(answer.get('choice'), 0) >= THRESHOLD)


@dataclass(frozen=True)
class CatalogEntry:
    identifier: str
    name: str
    canonical_name: str
    schema_digest: str
    description: str
    family: str

    def public(self):
        return {'id': self.identifier, 'name': self.name, 'canonical_name': self.canonical_name,
                'schema_digest': self.schema_digest, 'description': self.description}


@dataclass(frozen=True)
class ToolCatalogSnapshot:
    owner: str | None
    session_id: str
    submission_id: str
    policy_revision: int
    authority_epoch: int
    entries: tuple[CatalogEntry, ...]
    bundles: tuple[tuple[str, tuple[str, ...]], ...]
    catalog_digest: str

    @classmethod
    def freeze(cls, bound, inventory):
        """Inventory MUST already be permission filtered by its canonical owner."""
        if not isinstance(inventory, (list, tuple)):
            raise DecisionError('tool_inventory_unavailable')
        entries, names, groups = [], set(), {}
        for schema in inventory:
            if not isinstance(schema, dict):
                raise DecisionError('tool_catalog_invalid')
            fn = schema.get('function') if schema.get('type') == 'function' else None
            name = fn.get('name') if isinstance(fn, dict) else None
            if not isinstance(name, str) or not name or len(name) > 512 or name in names:
                raise DecisionError('tool_catalog_invalid')
            names.add(name)
            canonical = schema.get('canonical_name', name)
            if not isinstance(canonical, str) or not canonical or len(canonical) > 512:
                raise DecisionError('tool_catalog_invalid')
            description = fn.get('description', '')
            if not isinstance(description, str):
                raise DecisionError('tool_catalog_invalid')
            family = schema.get('routing_family') or _family(canonical)
            if not isinstance(family, str) or not family or len(family) > 160:
                raise DecisionError('tool_catalog_invalid')
            fingerprint = _hash(schema)
            identifier = 't_' + _hash([name, canonical, fingerprint])[:24]
            entries.append(CatalogEntry(identifier, name, canonical, fingerprint, description[:240], family))
        entries.sort(key=lambda item: item.name)
        for item in entries:
            groups.setdefault(item.family, []).append(item.identifier)
        # Bound choice cardinality without dropping any complete membership.
        if len(groups) > 120:
            ordered = sorted(groups)
            retained = {key: groups[key] for key in ordered[:119]}
            retained['other_dynamic'] = [i for key in ordered[119:] for i in groups[key]]
            groups = retained
        bundles = tuple(('b_' + _hash(family)[:20], tuple(ids)) for family, ids in sorted(groups.items()))
        payload = {'entries': [i.public() for i in entries],
                   'bundles': {key: list(ids) for key, ids in bundles}}
        return cls(bound.owner, bound.session_id, bound.submission_id,
                   bound.policy['policy_revision'], bound.policy['authority_epoch'],
                   tuple(entries), bundles, _hash(payload))

    def public(self):
        return {'digest': self.catalog_digest, 'catalog': [i.public() for i in self.entries],
                'bundles': {key: list(ids) for key, ids in self.bundles},
                'all_available': [i.identifier for i in self.entries]}

    def validate(self, bound, inventory=None):
        if ((self.owner, self.session_id, self.submission_id, self.policy_revision, self.authority_epoch)
                != (bound.owner, bound.session_id, bound.submission_id,
                    bound.policy['policy_revision'], bound.policy['authority_epoch']) or not bound.current()):
            raise DecisionError('tool_selection_stale')
        if inventory is not None and self.freeze(bound, inventory).catalog_digest != self.catalog_digest:
            raise DecisionError('tool_catalog_changed')


def _family(name):
    if name.startswith('mcp__'):
        return '__'.join(name.split('__')[:2])
    if name.startswith('mcp_'):
        return 'dynamic_mcp'
    for family, words in (
        ('files', ('file', 'glob', 'grep', 'directory', 'workspace', 'bash', 'shell', 'python', 'ls')),
        ('web', ('web', 'search', 'fetch', 'browser')),
        ('mail', ('mail', 'sender', 'attachment')),
        ('calendar', ('calendar', 'event')),
        ('documents', ('document', 'spreadsheet', 'presentation', 'slide')),
        ('control', ('ask_user', 'update_plan', 'task', 'skill')),
    ):
        if any(word in name for word in words):
            return family
    return 'other_registered'


@dataclass(frozen=True)
class ToolSelection:
    kind: str
    selected_names: frozenset[str]
    catalog: ToolCatalogSnapshot
    choice_id: str
    attempt_id: str | None = None
    confidence: float | None = None
    probabilities_json: bytes = b'{}'

    @property
    def catalog_digest(self):
        return self.catalog.catalog_digest

    @property
    def selection_digest(self):
        return _hash([self.kind, sorted(self.selected_names), self.catalog_digest, self.choice_id])

    def public(self):
        import json
        return {'kind': self.kind, 'selected_names': sorted(self.selected_names),
                'catalog_digest': self.catalog_digest, 'selection_digest': self.selection_digest,
                'choice_id': self.choice_id, 'attempt_id': self.attempt_id,
                'confidence': self.confidence, 'probabilities': json.loads(self.probabilities_json)}

    def validate(self, bound, inventory=None):
        self.catalog.validate(bound, inventory)
        if not self.selected_names <= {entry.name for entry in self.catalog.entries}:
            raise DecisionError('tool_selection_invalid')


@dataclass(frozen=True)
class PreparationReceipt:
    owner: str | None
    session_id: str
    submission_id: str
    evidence_json: bytes

    def evidence(self):
        import json
        return json.loads(self.evidence_json)

    def public(self):
        value = self.evidence()
        return {**value, 'sources': [{k: v for k, v in source.items() if k != 'excerpt'}
                                    for source in value['sources']], 'digest': _hash(value)}

    def validate(self, bound):
        if (self.owner, self.session_id, self.submission_id) != (bound.owner, bound.session_id, bound.submission_id):
            raise DecisionError('preparation_owner_mismatch')


def preparation_receipt(bound, sources=(), *, unknowns=()):
    """Describe already returned canonical sources; never read/fetch a source here."""
    if not isinstance(sources, (list, tuple)) or len(sources) > 32:
        raise DecisionError('preparation_invalid')
    prepared, budget = [], 16 * 1024
    for source in sources:
        if (not isinstance(source, dict) or source.get('owner', bound.owner) != bound.owner
                or source.get('session_id', bound.session_id) != bound.session_id
                or not all(isinstance(source.get(k), str) and source[k] for k in ('source_id', 'version', 'excerpt'))):
            raise DecisionError('preparation_source_invalid')
        raw = source['excerpt'].encode('utf-8')
        if len(raw) > budget:
            raise DecisionError('preparation_too_large')
        budget -= len(raw)
        coverage = source.get('coverage', 'partial')
        trust = source.get('trust', 'canonical_returned_source')
        if trust not in {'canonical_returned_source', 'untrusted_history', 'owner_maintained_summary'}:
            raise DecisionError('preparation_source_invalid')
        if coverage not in {'complete', 'partial', 'prefix'}:
            raise DecisionError('preparation_source_invalid')
        prepared.append({'source_id': source['source_id'], 'version': source['version'],
            'excerpt': source['excerpt'], 'returned_bytes': len(raw),
            'returned_sha256': hashlib.sha256(raw).hexdigest(), 'hash_scope': 'returned_excerpt',
            'coverage': coverage, 'trust': trust})
    if (not isinstance(unknowns, (list, tuple)) or len(unknowns) > 32
            or any(not isinstance(value, str) or len(value) > 512 for value in unknowns)):
        raise DecisionError('preparation_invalid')
    return PreparationReceipt(bound.owner, bound.session_id, bound.submission_id,
        encode({'sources': prepared, 'unknowns': list(unknowns), 'returned_bytes': 16 * 1024 - budget,
                'coverage': 'unknown' if not prepared else 'returned_sources_only'}))


@dataclass(frozen=True)
class FollowupDecision:
    relation: str
    previous_submission_id: str | None
    original_task_id: str | None
    linked_card_id: str | None
    confidence: float
    probabilities_json: bytes
    attempt_id: str

    def public(self):
        import json
        return {'relation': self.relation, 'previous_submission_id': self.previous_submission_id,
                'original_task_id': self.original_task_id, 'linked_card_id': self.linked_card_id,
                'confidence': self.confidence, 'probabilities': json.loads(self.probabilities_json),
                'attempt_id': self.attempt_id, 'intent_only': True}


async def prepare_tool_routing(bound, inventory, *, preparation=None, provider=None, semantic_only=False):
    """Run once before context/tool preprocessing, then bind an explicit selection."""
    from src.chat_automation import SqlAttemptStore
    from src.decisions import DecisionProvider
    if bound.tool_selection is not None:
        bound.tool_selection.validate(bound, inventory)
        return bound.tool_selection
    if semantic_only and getattr(bound, '_semantic_prepared', False):
        return None
    catalog = ToolCatalogSnapshot.freeze(bound, inventory)
    receipt = preparation or bound.preparation or preparation_receipt(bound)
    receipt.validate(bound)
    bound.preparation = receipt
    mode = bound.policy.get('tool_routing', 'existing')
    followup_enabled = bool(bound.policy.get('followup_enabled')) and bool(bound.prior_task) and not getattr(bound, '_semantic_prepared', False)
    diagnostics_enabled = bool(bound.policy.get('preparation_enabled')) and not getattr(bound, '_semantic_prepared', False)
    questions = {}
    choices = {'no_tools': 'No tools are needed for this turn.', 'abstain': 'Cannot safely choose from these tools.'}
    if mode == 'jev':
        choices.update({key: 'Use the complete bundle listed under this ID in state.bundles.' for key, _ in catalog.bundles})
        if catalog.entries:
            choices['all_available'] = 'Use the exact union of all tools in state.catalog; no wildcard expansion.'
        questions['tools'] = choices
    if followup_enabled:
        questions['followup'] = RELATIONS
    if diagnostics_enabled:
        questions.update({key: {choice: description + ' ' + DIAGNOSTICS[key]
                                for choice, description in YES_NO.items()} for key in DIAGNOSTICS})
    answer, decision = None, None
    if questions:
        first, primary = next(iter(questions.items()))
        request = DecisionRequest('turn_preparation', {'ask': bound.raw_ask,
            'previous': bound.prior_task, 'preparation': receipt.evidence(), **catalog.public()},
            primary, question_id=first,
            additional_questions={k: v for k, v in questions.items() if k != first},
            instructions=('Select each question independently. Returned source membership is exhaustive only '
                          'for sources actually returned. Future research TODOs do not require searching now. '
                          'Image pixels are unavailable unless explicitly returned. Follow-up is intent only; '
                          'confirmation cannot approve, revive, consume, or extend any card.'))
        provider = provider or DecisionProvider(store=SqlAttemptStore(bound.store))
        try:
            decision = await provider.evaluate(request, decision_context(bound),
                config=bound.configuration.get('decision_provider', {}))
        except DecisionError as error:
            if error.code == 'decision_request_too_large':
                raise DecisionError('tool_catalog_too_large', attempt_id=error.attempt_id) from None
            raise
        catalog.validate(bound, inventory)
        if mode == 'jev':
            answer = decision.answers.get('tools')
            if not _accepted(answer) or answer.get('choice') == 'abstain':
                raise DecisionError('tool_manual_selection_required', attempt_id=decision.attempt_id)
        if followup_enabled:
            followup = decision.answers.get('followup', {})
            if _accepted(followup):
                previous = bound.prior_task
                pending = previous.get('pending_question') or {}
                relation = followup.get('choice', 'unknown')
                # Confirmation without a canonical pending card has no special status.
                if relation == 'confirmation' and not pending:
                    relation = 'unknown'
                bound.followup = FollowupDecision(relation, previous.get('submission_id'),
                    previous.get('task_lineage_id'), pending.get('card_id'), followup.get('confidence', 0),
                    encode(followup.get('probabilities', {})), decision.attempt_id)
        if diagnostics_enabled:
            bound.diagnostics = {key: decision.answers.get(key, {}) for key in DIAGNOSTICS}
    if mode == 'jev':
        choice = answer['choice']
        identifiers = (dict(catalog.bundles).get(choice, ()) if choice != 'all_available'
                       else tuple(item.identifier for item in catalog.entries))
        names = frozenset(item.name for item in catalog.entries if item.identifier in identifiers)
        selection = ToolSelection('no_tools' if choice == 'no_tools' else 'all_available' if choice == 'all_available' else 'bundle',
            names, catalog, choice, decision.attempt_id, answer['confidence'], encode(answer['probabilities']))
    else:
        selection = ToolSelection('all_available' if mode == 'all' else 'existing',
                                  frozenset(item.name for item in catalog.entries), catalog, mode)
    if followup_enabled or diagnostics_enabled:
        bound._semantic_prepared = True
    if not semantic_only:
        bound.tool_selection = selection
    bound.store.record_preparation(bound)
    return selection if not semantic_only else None


async def select_effort(bound, selected_route, supported_efforts, *, provider=None):
    """Condition the effort decision on the admitted selected execution route."""
    from src.chat_automation import SqlAttemptStore
    from src.decisions import DecisionProvider
    allowed = tuple(dict.fromkeys(supported_efforts or ()))
    from src.approach_routing import EFFORTS
    if not allowed or any(value not in EFFORTS for value in allowed):
        bound.effort_selection = {'status': 'unavailable', 'reason': 'supported_efforts_unknown', 'source': 'mapped'}
        return selected_route.get('reasoning_effort') or ''
    if len(allowed) == 1:
        bound.effort_selection = {'status': 'bound', 'source': 'only_supported_effort', 'choice': allowed[0]}
        return allowed[0]
    request = DecisionRequest('execution_effort', {'ask': bound.raw_ask,
        'selected_route': {key: selected_route.get(key) for key in ('endpoint_id', 'model', 'approach_id')},
        'preparation': bound.preparation.evidence()},
        {key: {'none': 'No additional reasoning.', 'minimal': 'Minimal reconsideration.',
               'low': 'Known approach with little reconsideration, even if work is long or complex.',
               'medium': 'Some exploration or reconciliation.', 'high': 'Uncertain approach or competing hypotheses.',
               'xhigh': 'Deeply interacting uncertain constraints.', 'max': 'Exceptional unresolved novel reasoning.'}[key]
         for key in allowed}, question_id='effort',
        instructions='Choose effort independently of task length and approach category, for this selected route only.')
    decision = await (provider or DecisionProvider(store=SqlAttemptStore(bound.store))).evaluate(
        request, decision_context(bound), config=bound.configuration.get('decision_provider', {}))
    answer = decision.answers.get('effort', {})
    if not _accepted(answer):
        raise DecisionError('effort_manual_selection_required', attempt_id=decision.attempt_id)
    bound.effort_selection = {'status': 'bound', 'source': 'auto', 'choice': answer['choice'],
        'confidence': answer['confidence'], 'probabilities': dict(answer['probabilities']), 'attempt_id': decision.attempt_id}
    return answer['choice']
