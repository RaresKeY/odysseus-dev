"""Apply provider contracts and exact-route model evidence to requests."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from urllib.parse import urlsplit
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_CHATGPT_SUBSCRIPTION,
    stable_model_id_for,
)

DIALECT_CODEX_RESPONSES = "codex_responses"
DIALECT_OPENAI_CHAT = "openai_chat"

GOOGLE_CAPABILITY_REVISION = 'google-openai-thinking/2026-10-25-v2'
# Exact canonical-provider contracts, not model-name family guesses or routing
# rankings. Evidence: ai.google.dev/gemini-api/docs/openai#thinking and
# /docs/thinking plus /docs/generate-content/gemini-3 (reviewed 2026-10-25).
GOOGLE_EFFORTS = {
    'gemini-2.5-pro': ('minimal', 'low', 'medium', 'high'),
    'gemini-2.5-flash': ('none', 'minimal', 'low', 'medium', 'high'),
    'gemini-2.5-flash-lite': ('none', 'minimal', 'low', 'medium', 'high'),
    'gemini-3-pro-preview': ('low', 'high'),
    'gemini-3-flash-preview': ('minimal', 'low', 'medium', 'high'),
    'gemini-3.1-pro-preview': ('low', 'medium', 'high'),
    'gemini-3.1-flash-lite': ('minimal', 'low', 'medium', 'high'),
}

_CODEX_TEMPERATURE = mc.DeterministicControl.build(
    control=mc.CONTROL_TEMPERATURE,
    status=mc.ASSERTION_UNSUPPORTED,
    source=mc.SOURCE_PROVIDER_CONTRACT,
    confidence=mc.CONFIDENCE_EXPLICIT,
    evidence={
        "provider": VENDOR_CHATGPT_SUBSCRIPTION,
        "dialect": DIALECT_CODEX_RESPONSES,
        "reference": "https://github.com/odysseus-dev/odysseus/issues/6273",
        "reason": "The subscription Responses endpoint rejects temperature.",
    },
)


@dataclass(frozen=True)
class RequestControls:
    """Control evidence cannot change transport selection or tool authority."""

    vendor: str
    dialect: str
    model: str
    endpoint_scope: str = ""
    record: ModelCapabilityRecord | None = None
    contract_controls: tuple[mc.DeterministicControl, ...] = ()

    def _model_controls(self, control: str) -> tuple[mc.DeterministicControl, ...]:
        record = self.record
        if (
            not self.endpoint_scope
            or record is None
            or record.vendor != self.vendor
            or record.model_id != self.model
            or record.stable_model_id != stable_model_id_for(
                self.vendor, self.model, endpoint_id=self.endpoint_scope
            )
        ):
            return ()
        return tuple(
            item for item in record.deterministic_controls
            if item.control == control
        )

    def evidence_for(self, control: str) -> tuple[mc.DeterministicControl, ...]:
        """Return applicable evidence, with a transport prohibition first."""
        if (
            self.vendor == VENDOR_CHATGPT_SUBSCRIPTION
            and self.dialect == DIALECT_CODEX_RESPONSES
            and control == mc.CONTROL_TEMPERATURE
        ):
            return (_CODEX_TEMPERATURE, *self._model_controls(control))
        return tuple(item for item in self.contract_controls if item.control == control) + self._model_controls(control)

    def permits(self, control: str, value: Any) -> bool:
        """Reject unsupported values; unknown evidence preserves caller behavior."""
        for item in self.evidence_for(control):
            if item.status == mc.ASSERTION_UNSUPPORTED:
                return False
            if item.status not in {mc.ASSERTION_CLAIMED, mc.ASSERTION_VERIFIED}:
                continue
            evidence = dict(item.evidence)
            if "allowed_values" not in evidence:
                continue
            allowed = evidence["allowed_values"]
            # Malformed positive constraints cannot authorize an override.
            if not isinstance(allowed, (list, tuple)) or not allowed:
                return False
            if not any(
                type(value) is type(candidate) and value == candidate
                for candidate in allowed
            ):
                return False
        return True

    def effort_offer(self, known_efforts: tuple[str, ...]) -> dict:
        """Only explicit positive values can enter an Auto decision offer."""
        evidence = self.evidence_for(mc.CONTROL_REASONING_EFFORT)
        advertised = []
        for item in evidence:
            values = dict(item.evidence).get('allowed_values')
            if (item.status in {mc.ASSERTION_CLAIMED, mc.ASSERTION_VERIFIED}
                    and isinstance(values, (list, tuple)) and len(values) <= 32):
                for value in values:
                    if isinstance(value, str) and value in known_efforts and value not in advertised:
                        advertised.append(value)
        efforts = [value for value in advertised if self.permits(mc.CONTROL_REASONING_EFFORT, value)]
        revision = hashlib.sha256(json.dumps({
            'vendor': self.vendor, 'dialect': self.dialect,
            'model': self.model, 'evidence': [item.to_dict() for item in evidence],
        }, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
        return {'supported_efforts': efforts or ['default'], 'capability_revision': 'canonical/v1/' + revision}


def _google_contract(url, model, dialect):
    # A documented contract applies only to Google's exact compatible route.
    # Native catalog aliases do not alter the selected request identity.
    try:
        parts = urlsplit(url)
        canonical = (parts.scheme == 'https' and parts.hostname == 'generativelanguage.googleapis.com'
            and parts.port in (None, 443) and parts.username is None and parts.password is None
            and not parts.query and not parts.fragment
            and parts.path.rstrip('/') in {'/v1beta/openai', '/v1beta/openai/chat/completions'})
    except (ValueError, TypeError):
        canonical = False
    values = GOOGLE_EFFORTS.get(model.removeprefix('models/')) if isinstance(model, str) else None
    if not canonical or dialect != DIALECT_OPENAI_CHAT or not values:
        return ()
    return (mc.DeterministicControl.build(
        control=mc.CONTROL_REASONING_EFFORT, status=mc.ASSERTION_CLAIMED,
        source=mc.SOURCE_PROVIDER_CONTRACT, confidence=mc.CONFIDENCE_EXPLICIT,
        evidence={'allowed_values': list(values), 'revision': GOOGLE_CAPABILITY_REVISION},
    ),)


def request_controls_for(url, model, headers=None, *, dialect, provider=None):
    """Bind canonical provider contracts and fresh evidence to one exact route."""
    from src.model_capability_cache import cached_model_record, scope_for_endpoint
    from src.provider_capability_schemas import resolve_provider
    from src.chatgpt_subscription import is_chatgpt_subscription_base
    vendor = resolve_provider(base_url=url).provider_id
    if is_chatgpt_subscription_base(url):
        vendor = VENDOR_CHATGPT_SUBSCRIPTION
    record = cached_model_record(model, base_url=url, headers=headers)
    if vendor in {'unknown', 'generic_openai'} and record is not None and not record.fallback:
        vendor = record.vendor
    if record is not None and record.vendor != vendor:
        record = None
    return RequestControls(
        vendor=vendor, dialect=dialect, model=model,
        endpoint_scope=scope_for_endpoint(url, headers), record=record,
        contract_controls=_google_contract(url, model, dialect),
    )
