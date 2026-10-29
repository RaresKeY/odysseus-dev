import asyncio
import copy
import json

import pytest

from src import llm_core
from src.llm_usage import (
    aggregate_cache_usage,
    has_cache_usage,
    normalize_anthropic_usage,
    normalize_usage_counts,
    openai_cache_usage,
)


@pytest.mark.parametrize("bad", [None, True, -1, 0.5, "3", float("nan"), float("inf"), 2**63])
def test_invalid_cache_counter_does_not_destroy_valid_totals(bad):
    assert normalize_usage_counts(100, 5, cache_usage={"cache_read_input_tokens": bad}) == {
        "input_tokens": 100, "output_tokens": 5,
    }


def test_cache_counters_are_subsets_not_additional_openai_input():
    assert normalize_usage_counts(100, 5, cache_usage={
        "cache_read_input_tokens": 80, "cache_creation_input_tokens": 10,
    }) == {
        "input_tokens": 100, "output_tokens": 5,
        "cache_read_input_tokens": 80, "cache_creation_input_tokens": 10,
    }
    assert normalize_usage_counts(100, 5, cache_usage={
        "cache_read_input_tokens": 80, "cache_creation_input_tokens": 30,
    }) == {"input_tokens": 100, "output_tokens": 5}


def test_anthropic_counts_all_three_input_categories_once():
    assert normalize_anthropic_usage({
        "input_tokens": 10, "output_tokens": 5,
        "cache_read_input_tokens": 80, "cache_creation_input_tokens": 20,
    }) == {
        "input_tokens": 110, "output_tokens": 5,
        "cache_read_input_tokens": 80, "cache_creation_input_tokens": 20,
    }


@pytest.mark.parametrize("bad", [True, "8", -1, float("nan"), 2**63])
def test_anthropic_invalid_additive_counts_are_not_underreported(bad):
    assert normalize_anthropic_usage({"input_tokens": 10, "cache_read_input_tokens": bad}) is None


def test_anthropic_total_overflow_is_rejected():
    assert normalize_anthropic_usage({"input_tokens": 2**63 - 1, "cache_read_input_tokens": 1}) is None


def test_missing_cache_usage_is_distinct_from_zero():
    assert aggregate_cache_usage([]) == {}
    assert aggregate_cache_usage([{"cache_read_input_tokens": 30}, {}]) == {}
    assert aggregate_cache_usage([{"cache_read_input_tokens": 0}, {"cache_read_input_tokens": 0}]) == {"cache_read_input_tokens": 0}
    assert aggregate_cache_usage([{"cache_read_input_tokens": 30}, {"cache_read_input_tokens": 20}]) == {"cache_read_input_tokens": 50}
    assert "cache_read_input_tokens" not in normalize_anthropic_usage({"input_tokens": 10})


def test_openai_details_take_precedence_over_llama_timing_fallback():
    assert openai_cache_usage({"prompt_tokens_details": {"cached_tokens": 0}}, {"cache_n": 8}) == {"cache_read_input_tokens": 0}
    assert openai_cache_usage({"input_tokens_details": {"cached_tokens": 8}}) == {"cache_read_input_tokens": 8}
    assert openai_cache_usage({}, {"cache_n": 8}) == {"cache_read_input_tokens": 8}
    assert openai_cache_usage({}, {"prompt_n": 8}) == {}


def test_partial_route_usage_still_prevents_unsafe_cost_estimate():
    assert has_cache_usage({"usage_buckets": [{"cache_read_input_tokens": 80}, {}]})
    assert not has_cache_usage({"cache_read_input_tokens": 0})
    assert not has_cache_usage({"cache_read_input_tokens": True})


class _Response:
    status_code = 200

    def __init__(self, events):
        self.events = events

    async def aiter_lines(self):
        for event in self.events:
            yield "data: " + json.dumps(event)

    async def aread(self):
        return b""


class _Stream:
    def __init__(self, events):
        self.events = events

    async def __aenter__(self):
        return _Response(self.events)

    async def __aexit__(self, *args):
        return False


class _Client:
    def __init__(self, events):
        self.events = events

    def stream(self, *args, **kwargs):
        return _Stream(self.events)


def _stream_usage(monkeypatch, events, url):
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: _Client(events))
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda *a: False)
    monkeypatch.setattr(llm_core, "note_model_activity", lambda *a, **kw: None)
    monkeypatch.setattr(llm_core, "_clear_host_dead", lambda *a: None)

    async def collect():
        usages = []
        async for chunk in llm_core.stream_llm(url, "cache-fixture", [{"role": "user", "content": "hi"}]):
            for line in chunk.splitlines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                event = json.loads(line[6:])
                if event.get("type") == "usage":
                    usages.append(event["data"])
        return usages

    return asyncio.run(collect())


@pytest.mark.parametrize("extra", [
    {"usage": {"prompt_tokens_details": {"cached_tokens": 80}}},
    {"timings": {"cache_n": 80, "prompt_n": 20}},
])
def test_openai_and_llamacpp_streams_preserve_cache_read(monkeypatch, extra):
    event = {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 5}}
    event["usage"].update(extra.get("usage", {}))
    if "timings" in extra:
        event["timings"] = extra["timings"]
    assert _stream_usage(monkeypatch, [event], "http://127.0.0.1:8081/v1/chat/completions") == [{
        "input_tokens": 100, "output_tokens": 5, "cache_read_input_tokens": 80,
    }]


def test_anthropic_stream_merges_snapshots_without_double_counting(monkeypatch):
    events = [
        {"type": "message_start", "message": {"usage": {
            "input_tokens": 10, "output_tokens": 1,
            "cache_read_input_tokens": 80, "cache_creation_input_tokens": 20,
        }}},
        {"type": "message_delta", "usage": {
            "input_tokens": 12, "output_tokens": 5,
            "cache_read_input_tokens": 80, "cache_creation_input_tokens": 20,
        }},
        {"type": "message_delta", "usage": {"output_tokens": 9}},
        {"type": "message_stop"},
    ]
    assert _stream_usage(monkeypatch, events, "https://api.anthropic.com/v1/messages") == [{
        "input_tokens": 112, "output_tokens": 9,
        "cache_read_input_tokens": 80, "cache_creation_input_tokens": 20,
    }]


def test_anthropic_empty_usage_is_not_a_real_zero_sample(monkeypatch):
    events = [{"type": "message_start", "message": {"usage": {}}}, {"type": "message_stop"}]
    assert _stream_usage(monkeypatch, events, "https://api.anthropic.com/v1/messages") == []


def test_history_breakpoint_does_not_mutate_original_multimodal_content():
    messages = [
        {"role": "assistant", "content": "Earlier answer"},
        {"role": "user", "content": [{"type": "text", "text": "Follow up"}]},
    ]
    original = copy.deepcopy(messages)
    payload = llm_core._build_anthropic_payload("claude", messages, 0, 100)
    assert payload["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert messages == original


def test_history_breakpoint_covers_tool_results():
    messages = [
        {"role": "assistant", "content": "", "tool_calls": [{"id": "c1", "function": {"name": "read", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "c1", "content": "result"},
    ]
    payload = llm_core._build_anthropic_payload("claude", messages, 0, 100)
    assert payload["messages"][-1]["content"][-1] == {
        "type": "tool_result", "tool_use_id": "c1", "content": "result",
        "cache_control": {"type": "ephemeral"},
    }


def test_history_breakpoint_respects_existing_markers_and_thinking_blocks():
    payload = {"messages": [{"role": "assistant", "content": [
        {"type": "text", "text": "answer", "cache_control": {"type": "ephemeral", "ttl": "1h"}},
        {"type": "thinking", "thinking": "hidden", "signature": "fixture"},
    ]}]}
    before = copy.deepcopy(payload)
    llm_core._cache_anthropic_history(payload)
    assert payload == before


def test_history_breakpoint_does_not_exceed_four_markers():
    payload = {"messages": [{"role": "user", "content": [
        {"type": "text", "text": str(i), "cache_control": {"type": "ephemeral"}}
        for i in range(4)
    ]}, {"role": "user", "content": "new"}]}
    before = copy.deepcopy(payload)
    llm_core._cache_anthropic_history(payload)
    assert payload == before


def test_single_tiny_request_does_not_request_history_caching():
    payload = llm_core._build_anthropic_payload("claude", [{"role": "user", "content": "hi"}], 0, 100)
    assert payload["messages"][0]["content"] == "hi"


def test_responses_stream_preserves_input_token_details(monkeypatch):
    events = [{"type": "response.completed", "response": {"usage": {
        "input_tokens": 100, "output_tokens": 5,
        "input_tokens_details": {"cached_tokens": 80},
    }}}]
    assert _stream_usage(monkeypatch, events, "https://chatgpt.com/backend-api/codex/responses") == [{
        "input_tokens": 100, "output_tokens": 5, "cache_read_input_tokens": 80,
    }]


def test_history_checkpoint_precedes_transient_context_and_respects_limit():
    messages = [
        {"role": "system", "content": "stable instructions"},
        {"role": "user", "content": "earlier question"},
        {"role": "assistant", "content": "earlier answer"},
        {"role": "user", "content": "temporary retrieval and clock"},
        {"role": "user", "content": "current question"},
    ]
    original = copy.deepcopy(messages)
    tools = [{"type": "function", "function": {"name": "read", "parameters": {}}}]
    payload = llm_core._build_anthropic_payload("claude", messages, 0, 100, tools=tools)
    assert payload["messages"][1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    assert payload["messages"][-1]["content"][-1]["cache_control"] == {"type": "ephemeral"}
    blocks = payload["system"] + payload["tools"]
    for message in payload["messages"]:
        if isinstance(message["content"], list):
            blocks += message["content"]
    assert sum("cache_control" in block for block in blocks) == 4
    assert messages == original


@pytest.mark.parametrize("ttl", ["1h", "5m"])
@pytest.mark.parametrize("with_static_prefix", [False, True])
def test_caller_cache_controls_override_automatic_checkpoints(ttl, with_static_prefix):
    messages = [
        {"role": "system", "content": "stable instructions"},
        {"role": "user", "content": "old question"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": [{
            "type": "text", "text": "new question",
            "cache_control": {"type": "ephemeral", "ttl": ttl},
        }]},
    ]
    tools = [{"type": "function", "function": {"name": "read", "parameters": {}}}]
    if not with_static_prefix:
        messages = messages[1:]
        tools = None
    original = copy.deepcopy(messages)
    payload = llm_core._build_anthropic_payload("claude", messages, 0, 100, tools=tools)
    if with_static_prefix:
        assert "cache_control" not in payload["system"][0]
        assert "cache_control" not in payload["tools"][0]
    assert payload["messages"][1]["content"] == "old answer"
    assert payload["messages"][-1]["content"][0]["cache_control"] == {"type": "ephemeral", "ttl": ttl}
    assert messages == original


def test_caller_four_breakpoints_leave_no_automatic_static_markers():
    messages = [
        {"role": "system", "content": "stable instructions"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": [
            {"type": "text", "text": str(i), "cache_control": {"type": "ephemeral"}}
            for i in range(4)
        ]},
    ]
    tools = [{"type": "function", "function": {"name": "read", "parameters": {}}}]
    payload = llm_core._build_anthropic_payload("claude", messages, 0, 100, tools=tools)
    blocks = payload["system"] + payload["tools"]
    for message in payload["messages"]:
        if isinstance(message["content"], list):
            blocks += message["content"]
    assert sum("cache_control" in block for block in blocks) == 4
