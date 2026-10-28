"""Independent, one-call review using the originating conversation model.

Uses the existing provider payload adapters and durable decision accounting.
Text answers contain categorical assessments and an explicit uncertainty flag;
they never pretend to be Jev distributions. No tools, cache or route retries.
"""
from dataclasses import dataclass, field
import hashlib

from src.approval_policy import QUESTIONS, INSTRUCTIONS
from src.decisions.config import ResolvedDecisionEndpoint, decision_provider_availability, decision_url
from src.decisions.contracts import DecisionError, decode, encode, valid_usage
from src.decisions.provider import DecisionProvider

FALLBACK_INSTRUCTIONS = INSTRUCTIONS + "\nReturn only one JSON object with exactly risk_level, user_authorization, policy_check, effect_scope and uncertain. Use the offered labels and a boolean uncertain. Set uncertain=true whenever any assessment is uncertain. Do not include probabilities, confidence, rationale or tools."
FALLBACK_PROMPT_SHA256 = hashlib.sha256(FALLBACK_INSTRUCTIONS.encode()).hexdigest()


@dataclass(frozen=True)
class ConversationAssessment:
    labels: dict
    uncertain: bool
    usage: dict
    attempt_id: str
    provider_identity: dict
    # Generic observability must not invent distributions for this adapter.
    answers: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ConversationEndpoint(ResolvedDecisionEndpoint):
    provider_kind: str = ""

    def public_identity(self):
        return {**super().public_identity(), "distribution_source": "not_reported",
                "provider": self.provider_kind}


def model_snapshot(bound):
    """Seal both the resolved turn route and the saved conversation selection."""
    from src.chat_automation import _models, digest
    with bound.store.transaction() as db:
        bound.store.state(db, bound.owner, bound.session_id)
        row = db.get(_models().Session, bound.session_id)
        saved = {key: getattr(row, key) for key in ("model", "endpoint_id", "endpoint_url", "headers")}
    return digest({"saved": saved, "resolved": bound.descriptor,
                   "url": bound.endpoint_url, "headers": bound.headers})


def auto_approve_availability(config, owner, *, db=None, session_id=None, policy=None):
    primary = decision_provider_availability(config, owner, db=db)
    if primary["available"] or session_id is None:
        return primary
    from core import database as d
    from src.model_admission import require_owner_model
    from src.model_auto import validate_registered_candidates
    from contextlib import nullcontext
    try:
        with (nullcontext(db) if db is not None else d.SessionLocal()) as inventory:
            row = inventory.get(d.Session, session_id)
            from src.chat_automation import owner_key
            if row is None or owner_key(row.owner) != owner_key(owner):
                raise ValueError()
            if policy and policy.get("route_mode") == "auto":
                candidates = list((policy.get("auto_categories") or {}).values())
                if len(candidates) != 2:
                    raise ValueError()
                validate_registered_candidates(owner, candidates, db=inventory)
            else:
                if not row.model or not row.endpoint_url:
                    raise ValueError()
                require_owner_model(owner, row.model)
                if row.endpoint_id:
                    validate_registered_candidates(owner, [{"endpoint_id": row.endpoint_id, "model": row.model}], db=inventory)
                else:
                    decision_url(row.endpoint_url, "openai_json")  # Pure URL validation, no probe.
        return {"available": True, "reason_code": None, "reason": None,
                "reviewer_source": "conversation_model"}
    except Exception:
        return {**primary, "reason": "No usable reviewer is configured. Select an available conversation model or configure Jev in Chat settings."}


class ConversationReviewProvider(DecisionProvider):
    def __init__(self, store, bound, *, client_factory=None, observer=None):
        self.bound = bound
        self.origin = model_snapshot(bound)
        super().__init__(store, resolver=self._resolve, client_factory=client_factory, observer=observer)

    def _resolve(self, _config, owner):
        from src import llm_core as llm
        from src.model_admission import require_owner_model
        bound = self.bound
        if owner != bound.owner or not bound.current() or model_snapshot(bound) != self.origin:
            raise DecisionError("decision_endpoint_changed")
        model, identifier = bound.descriptor.get("model"), bound.descriptor.get("endpoint_id")
        if not model or not bound.endpoint_url:
            raise DecisionError("decision_endpoint_unavailable")
        require_owner_model(owner, model)
        url, headers = bound.endpoint_url, dict(bound.headers)
        if identifier:
            from src.endpoint_resolver import resolve_endpoint_by_id
            route = resolve_endpoint_by_id(identifier, model, owner=owner, require_exact_model=True)
            if not route or route != (url, model, headers):
                raise DecisionError("decision_endpoint_changed")
        provider = llm._detect_provider(url)
        if provider == "anthropic":
            target, headers, protocol = llm._normalize_anthropic_url(url), llm._build_anthropic_headers(headers), "approval_anthropic"
        elif provider == "ollama":
            target, protocol = llm._normalize_ollama_url(url), "approval_ollama"
        elif provider == "chatgpt-subscription":
            target, headers, protocol = llm._normalize_chatgpt_subscription_url(url), llm._provider_headers(provider, headers), "approval_responses"
        else:
            target, headers, protocol = llm._normalize_openai_chat_url(url), llm._provider_headers(provider, headers), "approval_openai"
        decision_url(target, "openai_json")  # Validate without replacing the adapter's target.
        return ConversationEndpoint(identifier or "conversation", model, protocol, target, headers,
                                    registration_revision=self.origin, provider_kind=provider)

    async def review(self, request, context):
        # The generic provider validates budgets and owns all reservation,
        # cancellation, TLS, response-size and settlement behavior.
        return await self.evaluate(request, context, config={
            "endpoint_id": self.bound.descriptor.get("endpoint_id") or "conversation",
            "model": self.bound.descriptor.get("model") or "unavailable",
            "protocol": "openai_json", "timeout_seconds": 30})

    def _payload(self, request, questions, endpoint):
        from src import llm_core as llm
        messages = [
            {"role": "system", "content": FALLBACK_INSTRUCTIONS},
            {"role": "user", "content": encode({"state": request.state, "choices": QUESTIONS}).decode()},
        ]
        if endpoint.protocol == "approval_anthropic":
            return llm._build_anthropic_payload(endpoint.model, messages, 0, 2048)
        if endpoint.protocol == "approval_ollama":
            # Avoid Ollama's implicit 2K window silently dropping policy/state.
            # A conservative byte bound covers input plus framing/output tokens.
            if len(encode(messages)) + 3072 > 16384:
                raise DecisionError("decision_request_too_large")
            return llm._build_ollama_payload(endpoint.model, messages, 0, 2048, stream=False, num_ctx=16384)
        if endpoint.protocol == "approval_responses":
            return llm._build_chatgpt_responses_payload(endpoint.model, messages, 0, 2048, stream=True,
                reasoning_effort=self.bound.descriptor.get("reasoning_effort") or None)
        token_key = "max_completion_tokens" if llm._uses_max_completion_tokens(endpoint.model) else "max_tokens"
        payload = {"model": endpoint.model, "messages": messages, "stream": False, token_key: 2048}
        # Plain JSON text is supported across chat providers; no schema feature
        # is assumed merely because a server exposes an OpenAI-shaped API.
        return payload

    def _read_response(self, wire, endpoint):
        if endpoint.protocol != "approval_responses":
            return decode(wire)
        completed = None
        for line in wire.decode("utf-8").splitlines():
            if not line.startswith("data:"):
                continue
            value = line[5:].strip()
            if value == "[DONE]":
                continue
            event = decode(value)
            if event.get("type") in {"error", "response.failed", "response.incomplete"}:
                raise DecisionError("decision_incomplete_response")
            if event.get("type") == "response.completed":
                if completed is not None:
                    raise DecisionError("decision_invalid_response")
                completed = event.get("response")
        if not isinstance(completed, dict):
            raise DecisionError("decision_incomplete_response")
        return completed

    def _usage(self, raw, endpoint):
        usage = raw.get("usage") or {}
        if endpoint.protocol == "approval_openai":
            return valid_usage({"input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")})
        if endpoint.protocol == "approval_ollama":
            return valid_usage({"input_tokens": raw.get("prompt_eval_count"), "output_tokens": raw.get("eval_count")})
        return valid_usage(usage)

    def _answers(self, raw, questions, endpoint):
        if raw.get("error") or raw.get("model") != endpoint.model or self._usage(raw, endpoint) is None:
            raise DecisionError("decision_invalid_response")
        if endpoint.protocol == "approval_openai":
            choices = raw.get("choices")
            if not isinstance(choices, list) or len(choices) != 1 or choices[0].get("finish_reason") != "stop":
                raise DecisionError("decision_incomplete_response")
            message = choices[0].get("message", {})
            if message.get("tool_calls") or message.get("function_call") or message.get("refusal"):
                raise DecisionError("decision_invalid_response")
            content = message.get("content")
        elif endpoint.protocol == "approval_ollama":
            if raw.get("done") is not True or raw.get("done_reason") not in {None, "stop"} or raw.get("message", {}).get("tool_calls"):
                raise DecisionError("decision_incomplete_response")
            content = raw.get("message", {}).get("content")
        else:
            blocks = raw.get("content") if endpoint.protocol == "approval_anthropic" else raw.get("output")
            if endpoint.protocol == "approval_anthropic":
                if raw.get("stop_reason") != "end_turn" or not isinstance(blocks, list) or any(b.get("type") != "text" for b in blocks):
                    raise DecisionError("decision_incomplete_response")
                content = "".join(b["text"] for b in blocks)
            else:
                if raw.get("status") != "completed" or not isinstance(blocks, list) or any(b.get("type") not in {"message", "reasoning"} for b in blocks):
                    raise DecisionError("decision_incomplete_response")
                parts = [p for b in blocks if b.get("type") == "message" for p in b.get("content", [])]
                if any(p.get("type") != "output_text" for p in parts):
                    raise DecisionError("decision_invalid_response")
                content = "".join(p["text"] for p in parts)
        parsed = decode(content)
        if (set(parsed) != {*QUESTIONS, "uncertain"} or type(parsed["uncertain"]) is not bool
                or any(not isinstance(parsed[k], str) or parsed[k] not in QUESTIONS[k] for k in QUESTIONS)):
            raise DecisionError("decision_invalid_answers")
        return parsed

    def _result(self, answers, request, measured, attempt_id, identity):
        return ConversationAssessment({key: answers[key] for key in QUESTIONS}, answers["uncertain"],
                                      measured, attempt_id, identity)
