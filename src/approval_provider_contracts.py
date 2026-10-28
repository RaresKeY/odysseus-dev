"""Approval-only provider contracts, independent of catalog/display heuristics.

These exact alias pairs were checked against the primary model pages on
2026-10-28. A changed upstream alias fails closed until its contract is reviewed.
They apply only to the documented HTTPS API origin, port and complete path;
proxies and similarly named models inherit no equivalence or schema capability.
"""
from dataclasses import dataclass
from urllib.parse import urlsplit

from src.approval_policy import QUESTIONS
from src.decisions.contracts import DecisionError

CONTRACT_REVISION = "approval-provider-contracts/2026-10-28"

# Each OpenAI pair is shown on https://developers.openai.com/api/docs/models/<alias>.
_OPENAI_ALIASES = {
    "gpt-4o": "gpt-4o-2024-08-06",
    "gpt-4o-mini": "gpt-4o-mini-2024-07-18",
    "gpt-5": "gpt-5-2025-08-07",
    "gpt-5-mini": "gpt-5-mini-2025-08-07",
    "gpt-5-nano": "gpt-5-nano-2025-08-07",
    "gpt-5.1": "gpt-5.1-2025-11-13",
    "gpt-5.2": "gpt-5.2-2025-12-11",
}
# https://platform.claude.com/docs/en/models/{sonnet,haiku}-4-5/overview
_ANTHROPIC_ALIASES = {
    "claude-sonnet-4-5": "claude-sonnet-4-5-20250929",
    "claude-haiku-4-5": "claude-haiku-4-5-20251001",
}


@dataclass(frozen=True)
class ApprovalProviderContract:
    response_models: tuple[str, ...]
    canonical_model: str
    output_format: str = "text_json"
    revision: str = CONTRACT_REVISION


def _official_url(url, host, path):
    parsed = urlsplit(url)
    return (parsed.scheme == "https" and parsed.hostname == host
            and parsed.port in {None, 443} and parsed.path == path
            and parsed.username is None and parsed.password is None
            and not parsed.query and not parsed.fragment)


def approval_provider_contract(protocol, url, model):
    """Resolve only local, server-owned facts; never probe or guess an alias."""
    if protocol not in {"approval_openai", "approval_anthropic", "approval_ollama", "approval_responses"}:
        raise DecisionError("decision_approval_contract_unsupported")
    models, canonical, output = (model,), model, "text_json"
    if protocol == "approval_openai" and _official_url(url, "api.openai.com", "/v1/chat/completions"):
        canonical = _OPENAI_ALIASES.get(model, model)
        models = tuple(dict.fromkeys((model, canonical)))
        # Explicit model contracts, not a GPT name/prefix or release-date guess.
        if model in {*_OPENAI_ALIASES, *_OPENAI_ALIASES.values(), "gpt-4o-2024-11-20"}:
            output = "openai_json_schema"
    elif protocol == "approval_anthropic" and _official_url(url, "api.anthropic.com", "/v1/messages"):
        canonical = _ANTHROPIC_ALIASES.get(model, model)
        models = tuple(dict.fromkeys((model, canonical)))
        # Messages structured outputs are documented for all supported models:
        # https://platform.claude.com/docs/en/build-with-claude/structured-outputs
        output = "anthropic_json_schema"
    elif protocol == "approval_ollama":
        if ":" not in model.rsplit("/", 1)[-1]:
            canonical, models = model + ":latest", (model, model + ":latest")
        # Native schema format is documented; Ollama Cloud explicitly lacks it.
        # https://docs.ollama.com/capabilities/structured-outputs
        if urlsplit(url).hostname != "ollama.com":
            output = "ollama_json_schema"
    elif protocol == "approval_openai" and _official_url(
            url, "generativelanguage.googleapis.com", "/v1beta/openai/chat/completions"):
        canonical = model.removeprefix("models/")
        models = tuple(dict.fromkeys((model, canonical, "models/" + canonical)))
    # The subscription Responses adapter has a narrower approved request field
    # contract than the public Responses API. Do not assume text.format support.
    parsed = urlsplit(url)
    expected = {"api.openai.com": ("approval_openai", "/v1/chat/completions"),
                "api.anthropic.com": ("approval_anthropic", "/v1/messages")}
    if parsed.hostname in expected and (protocol, parsed.path) != expected[parsed.hostname]:
        raise DecisionError("decision_approval_contract_unsupported")
    return ApprovalProviderContract(models, canonical, output)


def approval_json_schema():
    return {"type": "object", "additionalProperties": False,
            "properties": {**{name: {"type": "string", "enum": list(choices)}
                               for name, choices in QUESTIONS.items()},
                           "uncertain": {"type": "boolean"}},
            "required": [*QUESTIONS, "uncertain"]}


def apply_output_contract(payload, contract):
    """Shape one request; a rejected feature never triggers a retry."""
    schema = approval_json_schema()
    if contract.output_format == "openai_json_schema":
        payload["response_format"] = {"type": "json_schema", "json_schema": {
            "name": "odysseus_approval", "strict": True, "schema": schema}}
    elif contract.output_format == "anthropic_json_schema":
        payload["output_config"] = {"format": {"type": "json_schema", "schema": schema}}
    elif contract.output_format == "ollama_json_schema":
        payload["format"] = schema
    return payload
