"""Resolve one explicitly configured, owner-visible registered inference route."""
from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from urllib.parse import urlsplit, urlunsplit

from .contracts import DecisionError, encode


def admit_decision_owner(owner: str | None) -> str:
    """Missing authentication is never implicit local-operator authority."""
    from src.owner_identity import effective_storage_owner, auth_disabled, is_default_local_owner
    if owner is not None and not isinstance(owner, str):
        raise DecisionError("decision_owner_unavailable")
    admitted = effective_storage_owner(owner)
    if admitted is None or is_default_local_owner(admitted) and not auth_disabled():
        raise DecisionError("decision_owner_unavailable")
    return admitted


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


def public_provider_projection(identity: dict) -> dict:
    """Detach display metadata while retaining internal accounting identities."""
    public = dict(identity)
    # Conversation-route revisions include authentication headers; ordinary
    # registered decision revisions contain only the registration timestamp.
    if str(public.get("protocol") or "").startswith("approval_"):
        public.pop("configuration_digest", None)
    return public


def validate_config(config: dict) -> dict:
    permitted = {"endpoint_id", "model", "protocol", "timeout_seconds", "max_calls"}
    if (not isinstance(config, dict) or set(config) - permitted
            or any(not isinstance(config.get(k), str) or not config[k].strip()
                   or len(config[k]) > 512 for k in ("endpoint_id", "model"))
            or not isinstance(config.get("protocol"), str)
            or config["protocol"] not in {"systemone", "openai_json"}):
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


def resolve_decision_endpoint(config: dict, owner: str | None, *, db=None) -> ResolvedDecisionEndpoint:
    """Resolve locally, optionally within an existing settings transaction.

    No probes, credential refresh, raw URL/key overrides or ownerless fall-through.
    """
    config = validate_config(config)
    owner = admit_decision_owner(owner)
    from src.model_admission import require_owner_model
    try:
        require_owner_model(owner, config['model'])
    except ValueError:
        raise DecisionError('decision_model_not_allowed') from None
    from core.database import ModelEndpoint, SessionLocal
    from src.auth_helpers import owner_filter
    from src.endpoint_resolver import (_endpoint_enabled_models, _endpoint_hidden_models,
                                       build_headers, resolve_endpoint_runtime)

    owns_session = db is None
    if owns_session:
        db = SessionLocal()
    try:
        # Shared registrations are visible by the existing canonical policy;
        # an explicit endpoint ID is still mandatory. Empty-owner mode never
        # borrows a named owner's private registration or provider credential.
        query = db.query(ModelEndpoint).filter(ModelEndpoint.id == config["endpoint_id"],
                                               ModelEndpoint.is_enabled.is_(True))
        row = owner_filter(query, ModelEndpoint, owner).first()
        if row is None:
            raise DecisionError("decision_endpoint_unavailable")
        from src.model_admission import endpoint_supports_decisions
        if not endpoint_supports_decisions(row):
            raise DecisionError("decision_endpoint_protocol_unavailable")
        if getattr(row, "provider_auth_id", None):
            raise DecisionError("decision_endpoint_protocol_unavailable")
        model = config["model"]
        enabled = _endpoint_enabled_models(row)
        if model in _endpoint_hidden_models(row) or (enabled and model not in enabled):
            raise DecisionError("decision_model_unavailable")
        base, key = resolve_endpoint_runtime(row, owner=owner)
        url = decision_url(base, config["protocol"])
        # The hosted Jev API requires a key. The protocol itself does not:
        # self-hosted SystemOne and compatible JSON endpoints may be keyless.
        if (urlsplit(url).hostname or "").lower().rstrip(".") == "api.typesafe.ai" and not (
                isinstance(key, str) and key.strip()):
            raise DecisionError("decision_credentials_missing")
        # Credentials are obtained only from the selected encrypted registration.
        headers = build_headers(key, base) if key else {}
        return ResolvedDecisionEndpoint(
            endpoint_id=row.id, model=model, protocol=config["protocol"], url=url,
            headers=dict(headers), timeout_seconds=config["timeout_seconds"],
            max_calls=config["max_calls"], registration_revision=str(getattr(row, "updated_at", "") or ""))
    except DecisionError:
        raise
    except Exception:
        # Only explicit registration/credential checks establish unavailability.
        # An inventory or resolver defect must not authorize another reviewer.
        raise DecisionError("decision_admission_failed") from None
    finally:
        if owns_session:
            db.close()


def decision_provider_availability(config: dict | None, owner: str | None, *, db=None) -> dict:
    """Public configuration readiness; never a live health or key-validity probe."""
    reasons = {
        "decision_provider_not_configured": "Configure Jev credentials or select a compatible local decision model in Chat settings.",
        "decision_credentials_missing": "Add the Jev API key to the selected endpoint, or select a compatible local decision model.",
        "decision_endpoint_unavailable": "The selected decision endpoint is unavailable. Choose an enabled endpoint in Chat settings.",
        "decision_endpoint_protocol_unavailable": "The selected endpoint does not support decision review. Choose a compatible decision model.",
        "decision_model_unavailable": "The selected decision model is unavailable. Choose an enabled model in Chat settings.",
        "decision_model_not_allowed": "The selected decision model is not allowed for your account.",
        "decision_owner_unavailable": "Sign in before configuring automatic review.",
        "decision_endpoint_invalid": "The selected decision endpoint has an invalid configuration.",
        "decision_invalid_provider_budget": "The decision provider has invalid call limits. Update its settings.",
    }
    try:
        resolve_decision_endpoint(config, owner, db=db)
    except DecisionError as error:
        code = error.code if error.code in reasons else "decision_endpoint_unavailable"
        return {"available": False, "reason_code": code, "reason": reasons[code]}
    except Exception:
        # Do not expose database, endpoint or credential details in a UI response.
        return {"available": False, "reason_code": "decision_endpoint_unavailable",
                "reason": reasons["decision_endpoint_unavailable"]}
    return {"available": True, "reason_code": None, "reason": None}
