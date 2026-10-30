"""Apply provider contracts and exact-route model evidence to requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from src import model_capabilities as mc
from src.model_capability_readers.base import (
    ModelCapabilityRecord,
    VENDOR_CHATGPT_SUBSCRIPTION,
    stable_model_id_for,
)

DIALECT_CODEX_RESPONSES = "codex_responses"
DIALECT_OPENAI_CHAT = "openai_chat"

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
        return self._model_controls(control)

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
