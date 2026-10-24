"""Resolve one explicitly configured, owner-visible registered inference route."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from urllib.parse import urlsplit, urlunsplit

from .contracts import DecisionError, encode


@dataclass(frozen=True)
class ResolvedDecisionEndpoint:
    endpoint_id: str
    model: str
    protocol: str
    url: str = field(repr=False)
    headers: dict[str, str] = field(repr=False)
    timeout_seconds: float = 30
    max_calls: int = 32
    registration_revision: str = ""

    def public_identity(self) -> dict:
        facts = {"endpoint_id": self.endpoint_id, "model": self.model,
                 "protocol": self.protocol, "registration_revision": self.registration_revision}
        return {**{k: facts[k] for k in ("endpoint_id", "model", "protocol")},
                "configuration_digest": hashlib.sha256(encode({**facts, "url": self.url})).hexdigest(),
                "distribution_source": "provider_reported" if self.protocol == "systemone" else "model_reported"}


def validate_config(config: dict) -> dict:
    permitted = {"endpoint_id", "model", "protocol", "timeout_seconds", "max_calls"}
    if (not isinstance(config, dict) or set(config) - permitted
            or any(not isinstance(config.get(k), str) or not config[k].strip()
                   or len(config[k]) > 512 for k in ("endpoint_id", "model"))
            or config.get("protocol") not in {"systemone", "openai_json"}):
        raise DecisionError("decision_provider_not_configured")
    timeout, calls = config.get("timeout_seconds", 30), config.get("max_calls", 32)
    if (type(timeout) not in (int, float) or not 0.1 <= timeout <= 30
            or type(calls) is not int or not 1 <= calls <= 128):
        raise DecisionError("decision_invalid_provider_budget")
    return {"endpoint_id": config["endpoint_id"], "model": config["model"],
            "protocol": config["protocol"], "timeout_seconds": float(timeout), "max_calls": calls}


def decision_url(base: str, protocol: str) -> str:
    try:
        parts = urlsplit(base)
        if (parts.scheme not in {"http", "https"} or not parts.hostname
                or parts.username is not None or parts.password is not None
                or parts.query or parts.fragment or "\\" in base
                or any(c.isspace() or ord(c) < 32 for c in base)
                or any(p in {".", ".."} for p in parts.path.split("/"))):
            raise ValueError()
        _ = parts.port
        path = parts.path.rstrip("/")
        if protocol == "systemone":
            path = path if path.endswith("/v1/systemone") else path + ("/systemone" if path.endswith("/v1") else "/v1/systemone")
            return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
        if protocol == "openai_json":
            # Construction only: no DNS discovery, model probes or credential refresh.
            if path.endswith("/chat/completions"):
                return urlunsplit((parts.scheme, parts.netloc, path, "", ""))
            if not path:
                path = "/v1"
            return urlunsplit((parts.scheme, parts.netloc, path + "/chat/completions", "", ""))
    except (ValueError, TypeError, AttributeError):
        pass
    raise DecisionError("decision_endpoint_invalid")


def resolve_decision_endpoint(config: dict, owner: str | None) -> ResolvedDecisionEndpoint:
    """No raw URL/key overrides and no ownerless fall-through to named owners."""
    config = validate_config(config)
    from core.database import ModelEndpoint, SessionLocal
    from src.auth_helpers import owner_filter
    from src.owner_identity import DEFAULT_LOCAL_OWNER
    from src.endpoint_resolver import (_endpoint_enabled_models, _endpoint_hidden_models,
                                       build_headers, resolve_endpoint_runtime)

    db = SessionLocal()
    try:
        # Shared registrations are visible by the existing canonical policy;
        # an explicit endpoint ID is still mandatory. Empty-owner mode never
        # borrows a named owner's private registration or provider credential.
        query = db.query(ModelEndpoint).filter(ModelEndpoint.id == config["endpoint_id"],
                                               ModelEndpoint.is_enabled.is_(True))
        row = owner_filter(query, ModelEndpoint, owner or DEFAULT_LOCAL_OWNER).first()
        if row is None:
            raise DecisionError("decision_endpoint_unavailable")
        if getattr(row, "provider_auth_id", None):
            raise DecisionError("decision_endpoint_protocol_unavailable")
        model = config["model"]
        enabled = _endpoint_enabled_models(row)
        if model in _endpoint_hidden_models(row) or (enabled and model not in enabled):
            raise DecisionError("decision_model_unavailable")
        base, key = resolve_endpoint_runtime(row, owner=owner)
        url = decision_url(base, config["protocol"])
        # Credentials are obtained only from the selected encrypted registration.
        headers = build_headers(key, base) if key else {}
        return ResolvedDecisionEndpoint(
            endpoint_id=row.id, model=model, protocol=config["protocol"], url=url,
            headers=dict(headers), timeout_seconds=config["timeout_seconds"],
            max_calls=config["max_calls"], registration_revision=str(getattr(row, "updated_at", "") or ""))
    except DecisionError:
        raise
    except Exception:
        raise DecisionError("decision_endpoint_unavailable") from None
    finally:
        db.close()
