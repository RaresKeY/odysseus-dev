"""Three neutral approach IDs resolved to user-selected execution routes."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Collection

from src.decisions import DecisionContext, DecisionError, DecisionRequest
from src.decisions.contracts import MAX_TASK_BYTES

APPROACHES = {
    "established_procedure": "An established procedure achieves the task with a little adaptation.",
    "bounded_domain": "The approach is understood, with substantial work inside a bounded domain.",
    "approach_discovery": "Discover or reconsider an approach, connect domains, or reconsider the measurement.",
}
APPROACH_IDS = tuple(APPROACHES)
APPROACH_POLICY = "odysseus-approach/v1"
MINIMUM_CONFIDENCE = 0.80
EFFORTS = frozenset({"none", "minimal", "low", "medium", "high", "xhigh", "max"})


def validate_mapping(mapping: dict) -> dict:
    if (not isinstance(mapping, dict)
            or set(mapping) - {"endpoint_id", "model", "reasoning_effort", "supported_efforts"}
            or not {"endpoint_id", "model", "reasoning_effort"} <= set(mapping)
            or any(not isinstance(mapping.get(k), str) or not mapping[k].strip()
                   or len(mapping[k]) > 512 for k in ("endpoint_id", "model"))
            or mapping["reasoning_effort"] is not None and (
                not isinstance(mapping["reasoning_effort"], str)
                or mapping["reasoning_effort"] not in EFFORTS)):
        raise DecisionError("approach_mapping_invalid")
    if 'supported_efforts' in mapping:
        supported = mapping['supported_efforts']
        if (not isinstance(supported, list) or not 1 <= len(supported) <= len(EFFORTS)
                or any(not isinstance(value, str) or value not in EFFORTS for value in supported)
                or len(set(supported)) != len(supported)
                or mapping['reasoning_effort'] not in supported):
            raise DecisionError('approach_supported_efforts_invalid')
    return dict(mapping)


def validate_approaches(mappings: dict) -> dict:
    if not isinstance(mappings, dict) or set(mappings) != set(APPROACHES):
        raise DecisionError("approach_mappings_incomplete")
    return {key: validate_mapping(value) for key, value in mappings.items()}


@dataclass(frozen=True)
class ApproachSelection:
    approach_id: str
    attempt_id: str
    confidence: float
    probabilities: dict
    readiness: dict

    def public(self):
        return {"policy": APPROACH_POLICY, "approach_id": self.approach_id,
                "attempt_id": self.attempt_id, "confidence": self.confidence,
                "probabilities": self.probabilities, "readiness": self.readiness}


@dataclass(frozen=True)
class BoundExecutionRoute:
    owner: str | None
    endpoint_id: str
    model: str
    reasoning_effort: str | None
    url: str = field(repr=False)
    headers: dict = field(repr=False)
    descriptor: dict
    source: str
    approach_id: str | None = None
    attempt_id: str | None = None
    selection: dict = field(default_factory=dict)

    @property
    def endpoint_url(self):
        return self.url

    def public(self):
        return {"endpoint_id": self.endpoint_id, "model": self.model,
                "reasoning_effort": self.reasoning_effort, "source": self.source,
                "approach_id": self.approach_id, "attempt_id": self.attempt_id,
                "descriptor": dict(self.descriptor), "selection": dict(self.selection)}

    def to_dict(self):
        return {**self.public(), "category": self.approach_id}

    @property
    def category(self):
        return self.approach_id

    def matches_invocation(self, *, endpoint_url, model, headers, reasoning_effort):
        return (endpoint_url, model, dict(headers or {}), reasoning_effort) == (
            self.url, self.model, self.headers, self.reasoning_effort)


async def classify_approach(raw_ask: str, provider, context: DecisionContext, *, config: dict,
                            evidence: dict | None = None) -> ApproachSelection:
    if (not isinstance(raw_ask, str) or not raw_ask.strip()
            or len(raw_ask.encode("utf-8")) > MAX_TASK_BYTES):
        raise DecisionError("approach_ask_invalid")
    if evidence is not None and not isinstance(evidence, dict):
        raise DecisionError("approach_evidence_invalid")
    request = DecisionRequest(
        "approach", {"policy": APPROACH_POLICY, "ask": raw_ask, "evidence": evidence or {}},
        APPROACHES, question_id="approach", additional_questions={"readiness": {
            "ready": "The aim and decisive constraints permit a meaningful approach choice.",
            "needs_context": "Missing or contradictory decisive evidence prevents a meaningful approach choice."}},
        instructions=("Select approach and readiness independently. These are approach categories, "
                      "not vendor/model capability rankings. Task duration alone does not imply "
                      "approach discovery; ordinary implementation unknowns do not imply missing context."))
    decision = await provider.evaluate(request, context, config=config)
    readiness = decision.answers.get("readiness", {})
    if (decision.choice not in APPROACHES or decision.confidence < MINIMUM_CONFIDENCE
            or decision.probabilities.get(decision.choice, 0) < MINIMUM_CONFIDENCE
            or readiness.get("choice") != "ready" or readiness.get("confidence", 0) < MINIMUM_CONFIDENCE
            or readiness.get("probabilities", {}).get("ready", 0) < MINIMUM_CONFIDENCE):
        raise DecisionError("approach_manual_selection_required", attempt_id=decision.attempt_id)
    return ApproachSelection(decision.choice, decision.attempt_id, decision.confidence,
                             dict(decision.probabilities), dict(readiness))


def resolve_execution_route(mapping: dict, owner: str | None, *, allowed_models: Collection[str] | None = None,
                            source="manual", approach_id=None, attempt_id=None,
                            resolver=None, selection=None) -> BoundExecutionRoute:
    mapping = validate_mapping(mapping)
    from src.decisions.config import admit_decision_owner
    owner = admit_decision_owner(owner)
    from src.model_admission import require_owner_model
    try:
        require_owner_model(owner, mapping['model'])
    except ValueError:
        raise DecisionError('execution_model_not_allowed') from None
    if allowed_models is not None and mapping["model"] not in allowed_models:
        raise DecisionError("execution_model_not_allowed")
    if resolver is None:
        from src.endpoint_resolver import _resolve_endpoint_by_id_with_descriptor
        resolver = _resolve_endpoint_by_id_with_descriptor
    route = resolver(mapping["endpoint_id"], mapping["model"], owner=owner,
                     require_exact_model=True)
    if not route:
        raise DecisionError("execution_route_unavailable")
    candidate, descriptor = route
    if candidate[1] != mapping["model"] or descriptor.get("endpoint_id") != mapping["endpoint_id"]:
        raise DecisionError("execution_route_mismatch")
    descriptor = dict(descriptor)
    if 'supported_efforts' in mapping:
        descriptor['supported_efforts'] = list(mapping['supported_efforts'])
        descriptor['effort_support_source'] = 'owner_configured'
    return BoundExecutionRoute(owner, mapping["endpoint_id"], mapping["model"], mapping["reasoning_effort"],
                               candidate[0], dict(candidate[2] or {}), dict(descriptor), source,
                               approach_id, attempt_id, selection or {})


async def route_chat_turn(raw_ask: str, *, route_mode: str, owner: str | None,
                          context: DecisionContext, manual_mapping: dict | None = None,
                          approaches: dict | None = None, provider=None,
                          provider_config: dict | None = None, evidence: dict | None = None,
                          allowed_models: Collection[str] | None = None, resolver=None) -> BoundExecutionRoute:
    """Selection only. The owner must bind this descriptor at the actual call."""
    if not isinstance(raw_ask, str) or not raw_ask.strip():
        raise DecisionError("approach_ask_invalid")
    if context.owner != owner:
        raise DecisionError("execution_owner_mismatch")
    if route_mode == "manual":
        return resolve_execution_route(manual_mapping, owner, allowed_models=allowed_models, resolver=resolver)
    if route_mode != "auto" or provider is None or provider_config is None:
        raise DecisionError("approach_routing_not_configured")
    mappings = validate_approaches(approaches)
    # Validate all configured routes before the classification can incur spending.
    for mapping in mappings.values():
        resolve_execution_route(mapping, owner, allowed_models=allowed_models, resolver=resolver)
    decision = await classify_approach(raw_ask, provider, context, config=provider_config, evidence=evidence)
    return resolve_execution_route(mappings[decision.approach_id], owner, allowed_models=allowed_models,
                                   source="auto", approach_id=decision.approach_id,
                                   attempt_id=decision.attempt_id, resolver=resolver, selection=decision.public())


async def route_approach(raw_ask, mappings, *, provider, config, context, evidence=None):
    """Admission-service facade; returns a descriptor, never executes the ask."""
    return await route_chat_turn(raw_ask, route_mode="auto", owner=context.owner, context=context,
                                 approaches=mappings, provider=provider, provider_config=config, evidence=evidence)
