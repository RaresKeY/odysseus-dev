"""Full offline classifier-to-registered-route binding, with no task execution."""
import json

import httpx
import pytest

from src.approach_routing import APPROACHES, route_chat_turn
from src.decisions import DecisionContext, DecisionError, DecisionProvider, MemoryAttemptStore, ResolvedDecisionEndpoint
from src.decisions.contracts import decode

ASK = " \tKeep these edges.\nUnicode: café 🙂\n\n "
CONFIG = {"endpoint_id": "reviewer", "model": "neutral-decider", "protocol": "systemone"}
CONTEXT = DecisionContext("alice", "one", "one-run")
MAPPINGS = {key: {"endpoint_id": "endpoint-" + str(i), "model": "user-selected-" + str(i), "reasoning_effort": "low"}
            for i, key in enumerate(APPROACHES)}


@pytest.fixture(autouse=True)
def registered_model_owner(monkeypatch):
    from tests.helpers.model_admission import register_model_owners
    register_model_owners(monkeypatch, {
        "alice": {"neutral-decider", *(item["model"] for item in MAPPINGS.values())},
    })


def route_resolver(endpoint_id, model, *, owner, require_exact_model):
    assert owner == "alice" and require_exact_model is True
    return (("https://execution.invalid/v1/chat/completions", model, {"Authorization": "SYNTHETIC"}),
            {"endpoint_id": endpoint_id, "endpoint_label": "Configured model"})


def setup(readiness="ready", low=False):
    seen = []
    def transport(request):
        payload = decode(request.content)
        seen.append(payload)
        assert payload["state"]["ask"] == ASK
        assert "user-selected" not in json.dumps(payload)
        assert "endpoint-" not in json.dumps(payload)
        answers = {}
        for name, question in payload["questions"].items():
            selected = "bounded_domain" if name == "approach" else readiness
            probabilities = {key: float(key == selected) for key in question["criteria"]}
            answers[name] = {"type": "choice", "choice": selected, "confidence": .4 if low else .99,
                             "probabilities": probabilities}
        return httpx.Response(200, json={"model": "neutral-decider", "answers": answers,
                                        "usage": {"input_tokens": 20, "output_tokens": 10}})
    provider = DecisionProvider(MemoryAttemptStore(), resolver=lambda *_: ResolvedDecisionEndpoint(
        "reviewer", "neutral-decider", "systemone", "https://decision.invalid/v1/systemone", {}),
        client_factory=lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(transport), **kw))
    return provider, seen


@pytest.mark.asyncio
async def test_three_neutral_categories_bind_actual_user_selected_route_without_ask_rewrite():
    provider, seen = setup()
    route = await route_chat_turn(ASK, route_mode="auto", owner="alice", context=CONTEXT,
                                  approaches=MAPPINGS, provider=provider, provider_config=CONFIG,
                                  resolver=route_resolver)
    assert len(seen) == 1 and route.approach_id == "bounded_domain"
    assert route.model == "user-selected-1" and route.endpoint_id == "endpoint-1"
    assert route.matches_invocation(endpoint_url=route.url, model=route.model,
                                    headers={"Authorization": "SYNTHETIC"}, reasoning_effort="low")
    assert not route.matches_invocation(endpoint_url=route.url, model="different", headers={}, reasoning_effort="low")
    assert "SYNTHETIC" not in json.dumps(route.public())


@pytest.mark.asyncio
async def test_manual_selection_preserves_ask_and_does_not_classify():
    provider, seen = setup()
    route = await route_chat_turn(ASK, route_mode="manual", owner="alice", context=CONTEXT,
                                  manual_mapping=MAPPINGS["approach_discovery"], provider=provider,
                                  resolver=route_resolver)
    assert not seen and route.source == "manual" and route.model == "user-selected-2"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing_mapping", "forbidden_model", "unavailable_endpoint", "wrong_owner"])
async def test_invalid_configuration_fails_before_spending(kind):
    provider, seen = setup()
    mappings = dict(MAPPINGS)
    kwargs = {}
    if kind == "missing_mapping": mappings.pop("established_procedure")
    if kind == "forbidden_model": kwargs["allowed_models"] = {"different"}
    if kind == "unavailable_endpoint": kwargs["resolver"] = lambda *a, **k: None
    with pytest.raises(DecisionError):
        await route_chat_turn(ASK, route_mode="auto", owner="bob" if kind == "wrong_owner" else "alice",
                              context=CONTEXT, approaches=mappings, provider=provider,
                              provider_config=CONFIG, **{"resolver": route_resolver, **kwargs})
    assert not seen


@pytest.mark.asyncio
@pytest.mark.parametrize("ready,low", [("needs_context", False), ("ready", True)])
async def test_insufficient_or_uncertain_choice_needs_manual_selection(ready, low):
    provider, seen = setup(ready, low)
    with pytest.raises(DecisionError, match="manual_selection_required"):
        await route_chat_turn(ASK, route_mode="auto", owner="alice", context=CONTEXT,
                              approaches=MAPPINGS, provider=provider, provider_config=CONFIG,
                              resolver=route_resolver)
    assert len(seen) == 1
