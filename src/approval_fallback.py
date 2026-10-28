"""Independent, one-call review using the originating conversation model.

Uses the existing provider payload adapters and durable decision accounting.
Text answers contain categorical assessments and an explicit uncertainty flag;
they never pretend to be Jev distributions. No tools, approval cache or route retries.
"""
from dataclasses import dataclass, field
from collections import OrderedDict
import hashlib
from threading import RLock
import time

from src.approval_policy import QUESTIONS, INSTRUCTIONS
from src.approval_provider_contracts import (CONTRACT_REVISION, ApprovalProviderContract, approval_provider_contract,
                                            apply_output_contract)
from src.decisions.config import ResolvedDecisionEndpoint, decision_provider_availability, decision_url
from src.decisions.contracts import DecisionError, decode, encode, valid_usage
from src.decisions.provider import DecisionProvider

FALLBACK_INSTRUCTIONS = INSTRUCTIONS + "\nReturn only one JSON object with exactly risk_level, user_authorization, policy_check, effect_scope and uncertain. Use the offered labels and a boolean uncertain. Set uncertain=true whenever any assessment is uncertain. Do not include probabilities, confidence, rationale or tools."
FALLBACK_PROMPT_SHA256 = hashlib.sha256(FALLBACK_INSTRUCTIONS.encode()).hexdigest()

# Observed wire-contract failures only, scoped to the current saved selection,
# exact resolved model/registration, owner, chat and database. No answer or
# approval is cached. Expiry permits recovery without probing or a hidden retry.
_INCOMPATIBLE_SECONDS = 300
_INCOMPATIBLE_LIMIT = 256
_incompatible = OrderedDict()
_incompatible_lock = RLock()
_memo_clock = time.monotonic
_CONTRACT_FAILURES = {"decision_approval_model_mismatch", "decision_approval_usage_missing",
                      "decision_invalid_json", "decision_invalid_answers"}


def _binding_key(db, row, owner, model, identifier):
    from core import database as d
    registration = db.get(d.ModelEndpoint, identifier) if identifier else None
    auth = db.get(d.ProviderAuthSession, registration.provider_auth_id) if registration is not None and registration.provider_auth_id else None
    saved = {key: getattr(row, key) for key in ("model", "endpoint_id", "endpoint_url", "headers")}
    revision = {key: str(getattr(registration, key, "") or "") for key in (
        "id", "updated_at", "base_url", "provider_auth_id", "cached_models", "pinned_models", "hidden_models", "is_enabled")}
    seal = {"owner": owner, "session": row.id, "saved": saved,
            "model": model, "endpoint_id": identifier, "registration": revision,
            "auth_revision": str(getattr(auth, "updated_at", "") or ""),
            "contract": CONTRACT_REVISION}
    return db.get_bind(), hashlib.sha256(encode(seal)).hexdigest()


def _incompatibility(key, *, remember=None):
    with _incompatible_lock:
        now = _memo_clock()
        for expired in [item for item, (until, _) in _incompatible.items() if until <= now]:
            del _incompatible[expired]
        if remember is not None:
            _incompatible[key] = (now + _INCOMPATIBLE_SECONDS, remember)
            _incompatible.move_to_end(key)
            while len(_incompatible) > _INCOMPATIBLE_LIMIT:
                _incompatible.popitem(last=False)
        return _incompatible.get(key)


def _conversation_route(url, headers):
    from src import llm_core as llm
    decision_url(url, "openai_json")
    provider = llm._detect_provider(url)
    if provider == "anthropic":
        return llm._normalize_anthropic_url(url), llm._build_anthropic_headers(headers), "approval_anthropic", provider
    if provider == "ollama":
        return llm._normalize_ollama_url(url), headers, "approval_ollama", provider
    if provider == "chatgpt-subscription":
        return llm._normalize_chatgpt_subscription_url(url), llm._provider_headers(provider, headers), "approval_responses", provider
    return llm._normalize_openai_chat_url(url), llm._provider_headers(provider, headers), "approval_openai", provider


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
    contract: ApprovalProviderContract | None = None

    def public_identity(self):
        contract = self.contract or approval_provider_contract(self.protocol, self.url, self.model)
        return {**super().public_identity(), "distribution_source": "not_reported",
                "provider": self.provider_kind, "canonical_model": contract.canonical_model,
                "accepted_response_models": list(contract.response_models),
                "identity_contract": contract.revision, "output_contract": contract.output_format}


def model_snapshot(bound):
    """Seal both the resolved turn route and the saved conversation selection."""
    from src.chat_automation import _models, digest
    with bound.store.transaction() as db:
        bound.store.state(db, bound.owner, bound.session_id)
        row = db.get(_models().Session, bound.session_id)
        saved = {key: getattr(row, key) for key in ("model", "endpoint_id", "endpoint_url", "headers")}
    return digest({"saved": saved, "resolved": bound.descriptor,
                   "url": bound.endpoint_url, "headers": bound.headers,
                   "route_revision": getattr(bound, "review_route_revision", 0)})


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
                selections = [(item["model"], item["endpoint_id"], inventory.get(d.ModelEndpoint, item["endpoint_id"]).base_url)
                              for item in candidates]
            else:
                if not row.model or not row.endpoint_url:
                    raise ValueError()
                require_owner_model(owner, row.model)
                if row.endpoint_id:
                    validate_registered_candidates(owner, [{"endpoint_id": row.endpoint_id, "model": row.model}], db=inventory)
                else:
                    decision_url(row.endpoint_url, "openai_json")  # Pure URL validation, no probe.
                selections = [(row.model, row.endpoint_id, row.endpoint_url)]
            for model, identifier, url in selections:
                target, _, protocol, _ = _conversation_route(url, {})
                approval_provider_contract(protocol, target, model)
                if _incompatibility(_binding_key(inventory, row, owner, model, identifier)):
                    raise DecisionError("decision_approval_contract_incompatible")
        return {"available": True, "reason_code": None, "reason": None,
                "reviewer_source": "conversation_model"}
    except DecisionError as error:
        if error.code in {"decision_approval_contract_unsupported", "decision_approval_contract_incompatible"}:
            reason = ("The selected conversation model returned an incompatible approval response. Automatic review for this configuration is paused for up to five minutes."
                      if error.code == "decision_approval_contract_incompatible" else
                      "The selected conversation endpoint does not support the required approval response contract.")
            return {"available": False, "reason_code": error.code,
                    "reason": reason + " Choose another conversation model, configure Jev, or use Always Ask.",
                    "reviewer_source": "conversation_model"}
        return {**primary, "reason": "No usable reviewer is configured. Select an available conversation model or configure Jev in Chat settings."}
    except Exception:
        return {**primary, "reason": "No usable reviewer is configured. Select an available conversation model or configure Jev in Chat settings."}


class ConversationReviewProvider(DecisionProvider):
    def __init__(self, store, bound, *, client_factory=None, observer=None):
        self.bound = bound
        self.origin = model_snapshot(bound)
        self.binding_key = None
        super().__init__(store, resolver=self._resolve, client_factory=client_factory, observer=observer)

    def _resolve(self, _config, owner):
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
        target, headers, protocol, provider = _conversation_route(url, headers)
        decision_url(target, "openai_json")  # Validate without replacing the adapter's target.
        contract = approval_provider_contract(protocol, target, model)
        from src.chat_automation import _models
        with bound.store.transaction() as db:
            self.binding_key = _binding_key(db, db.get(_models().Session, bound.session_id), owner, model, identifier)
        if _incompatibility(self.binding_key):
            raise DecisionError("decision_approval_contract_incompatible")
        return ConversationEndpoint(identifier or "conversation", model, protocol, target, headers,
                                    registration_revision=self.origin, provider_kind=provider, contract=contract)

    async def review(self, request, context):
        # The generic provider validates budgets and owns all reservation,
        # cancellation, TLS, response-size and settlement behavior.
        try:
            return await self.evaluate(request, context, config={
                "endpoint_id": self.bound.descriptor.get("endpoint_id") or "conversation",
                "model": self.bound.descriptor.get("model") or "unavailable",
                "protocol": "openai_json", "timeout_seconds": 30})
        except DecisionError as error:
            if self.binding_key is not None and getattr(error, "dispatched", False) and error.code in _CONTRACT_FAILURES:
                _incompatibility(self.binding_key, remember=error.code)
            raise

    def _payload(self, request, questions, endpoint):
        from src import llm_core as llm
        messages = [
            {"role": "system", "content": FALLBACK_INSTRUCTIONS},
            {"role": "user", "content": encode({"state": request.state, "choices": QUESTIONS}).decode()},
        ]
        contract = endpoint.contract or approval_provider_contract(endpoint.protocol, endpoint.url, endpoint.model)
        if endpoint.protocol == "approval_anthropic":
            return apply_output_contract(llm._build_anthropic_payload(endpoint.model, messages, 0, 2048), contract)
        if endpoint.protocol == "approval_ollama":
            # Avoid Ollama's implicit 2K window silently dropping policy/state.
            # Include the schema and complete payload in the conservative byte
            # bound, with separate room for framing and output tokens.
            payload = apply_output_contract(llm._build_ollama_payload(
                endpoint.model, messages, 0, 2048, stream=False, num_ctx=16384), contract)
            if len(encode(payload)) + 3072 > 16384:
                raise DecisionError("decision_request_too_large")
            return payload
        if endpoint.protocol == "approval_responses":
            return llm._build_chatgpt_responses_payload(endpoint.model, messages, 0, 2048, stream=True,
                reasoning_effort=self.bound.descriptor.get("reasoning_effort") or None)
        token_key = "max_completion_tokens" if llm._uses_max_completion_tokens(endpoint.model) else "max_tokens"
        payload = {"model": endpoint.model, "messages": messages, "stream": False, token_key: 2048}
        return apply_output_contract(payload, contract)

    def _read_response(self, wire, endpoint):
        if endpoint.protocol != "approval_responses":
            return decode(wire)
        completed, data, event_name = None, [], None
        # SSE data can span multiple lines. Require a terminated frame and one
        # final completed response; a bare JSON response or partial stream is
        # never proof of completion.
        for line in wire.decode("utf-8").splitlines():
            if line.startswith("data:"):
                data.append(line[5:].lstrip(" "))
                continue
            if line.startswith("event:"):
                if event_name is not None:
                    raise DecisionError("decision_invalid_response")
                event_name = line[6:].strip()
                continue
            if line:
                continue
            if not data:
                event_name = None
                continue
            value = "\n".join(data)
            data = []
            if value == "[DONE]":
                if completed is None:
                    raise DecisionError("decision_incomplete_response")
                event_name = None
                continue
            event = decode(value)
            if event_name is not None and event_name != event.get("type"):
                raise DecisionError("decision_invalid_response")
            event_name = None
            if completed is not None:
                raise DecisionError("decision_invalid_response")
            if event.get("type") in {"error", "response.failed", "response.incomplete"}:
                raise DecisionError("decision_incomplete_response")
            item = event.get("item")
            part = event.get("part")
            if (str(event.get("type", "")).startswith(("response.function_call", "response.refusal"))
                    or isinstance(part, dict) and part.get("type") == "refusal"
                    or isinstance(item, dict) and item.get("type") not in {"message", "reasoning"}):
                raise DecisionError("decision_invalid_response")
            if event.get("type") == "response.completed":
                completed = event.get("response")
        if data or event_name is not None or not isinstance(completed, dict):
            raise DecisionError("decision_incomplete_response")
        return completed

    def _usage(self, raw, endpoint):
        usage = raw.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        if endpoint.protocol == "approval_openai":
            return valid_usage({"input_tokens": usage.get("prompt_tokens"), "output_tokens": usage.get("completion_tokens")})
        if endpoint.protocol == "approval_ollama":
            return valid_usage({"input_tokens": raw.get("prompt_eval_count"), "output_tokens": raw.get("eval_count")})
        if endpoint.protocol == "approval_anthropic" and isinstance(usage, dict):
            measured = valid_usage(usage)
            extra = [usage.get(key, 0) for key in ("cache_creation_input_tokens", "cache_read_input_tokens")]
            if measured is None or any(type(value) is not int or value < 0 for value in extra):
                return None
            return {**measured, "input_tokens": measured["input_tokens"] + sum(extra)}
        return valid_usage(usage)

    @staticmethod
    def _same_response_model(endpoint, reported):
        contract = endpoint.contract or approval_provider_contract(endpoint.protocol, endpoint.url, endpoint.model)
        return isinstance(reported, str) and reported in contract.response_models

    def _answers(self, raw, questions, endpoint):
        if raw.get("error"):
            raise DecisionError("decision_invalid_response")
        if not self._same_response_model(endpoint, raw.get("model")):
            raise DecisionError("decision_approval_model_mismatch")
        if self._usage(raw, endpoint) is None:
            raise DecisionError("decision_approval_usage_missing")
        if endpoint.protocol == "approval_openai":
            choices = raw.get("choices")
            if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict) or choices[0].get("finish_reason") != "stop":
                raise DecisionError("decision_incomplete_response")
            message = choices[0].get("message")
            if not isinstance(message, dict) or message.get("tool_calls") or message.get("function_call") or message.get("refusal"):
                raise DecisionError("decision_invalid_response")
            content = message.get("content")
        elif endpoint.protocol == "approval_ollama":
            message = raw.get("message")
            if raw.get("done") is not True or raw.get("done_reason") not in {None, "stop"} or not isinstance(message, dict) or message.get("tool_calls") or message.get("refusal"):
                raise DecisionError("decision_incomplete_response")
            content = message.get("content")
        else:
            blocks = raw.get("content") if endpoint.protocol == "approval_anthropic" else raw.get("output")
            if endpoint.protocol == "approval_anthropic":
                if raw.get("stop_reason") != "end_turn" or not isinstance(blocks, list) or any(not isinstance(b, dict) or b.get("type") != "text" or not isinstance(b.get("text"), str) for b in blocks):
                    raise DecisionError("decision_incomplete_response")
                content = "".join(b["text"] for b in blocks)
            else:
                if raw.get("status") != "completed" or not isinstance(blocks, list) or any(not isinstance(b, dict) or b.get("type") not in {"message", "reasoning"} for b in blocks):
                    raise DecisionError("decision_incomplete_response")
                if any(not isinstance(b.get("content"), list) or b.get("status", "completed") != "completed"
                       for b in blocks if b.get("type") == "message"):
                    raise DecisionError("decision_incomplete_response")
                parts = [p for b in blocks if b.get("type") == "message" for p in b.get("content", [])]
                if any(not isinstance(p, dict) or p.get("type") != "output_text" or not isinstance(p.get("text"), str) for p in parts):
                    raise DecisionError("decision_invalid_response")
                content = "".join(p["text"] for p in parts)
        parsed = decode(content)
        if (set(parsed) != {*QUESTIONS, "uncertain"} or type(parsed["uncertain"]) is not bool
                or any(not isinstance(parsed[k], str) or parsed[k] not in QUESTIONS[k] for k in QUESTIONS)):
            raise DecisionError("decision_invalid_answers")
        return {**parsed, "_reviewed_model": raw["model"]}

    def _result(self, answers, request, measured, attempt_id, identity):
        return ConversationAssessment({key: answers[key] for key in QUESTIONS}, answers["uncertain"],
                                      measured, attempt_id, {**identity, "reviewed_model": answers["_reviewed_model"]})
