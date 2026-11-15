import asyncio

import pytest

from src import llm_core


def _tool():
    return {
        "type": "function",
        "function": {
            "name": "search",
            "description": "search",
            "parameters": {"type": "object", "properties": {}},
        },
    }


class _Response:
    status_code = 200
    async def aiter_lines(self):
        yield 'data: {"choices":[{"delta":{"content":"OK"}}]}'
        yield 'data: [DONE]'
    async def __aenter__(self):
        return self
    async def __aexit__(self, *args):
        return False


@pytest.mark.parametrize("model", ["gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5-mini-2025-08-07", "gpt-5.6-luna"])
@pytest.mark.parametrize("effort", [None, "minimal", "low", "medium", "high"])
def test_streamed_openai_tools_preserve_selected_effort(monkeypatch, model, effort):
    seen = {}
    class Client:
        def stream(self, method, url, **kwargs):
            seen.update(kwargs["json"])
            return _Response()
    monkeypatch.setattr(llm_core, "_get_http_client", lambda: Client())
    monkeypatch.setattr(llm_core, "_is_host_dead", lambda url: False)
    async def run():
        return [chunk async for chunk in llm_core.stream_llm(
            "https://api.openai.com/v1/chat/completions", model,
            [{"role": "user", "content": "Hello"}], tools=[_tool()],
            reasoning_effort=effort)]
    chunks = asyncio.run(run())
    assert any("OK" in chunk for chunk in chunks)
    assert seen["model"] == model
    assert seen["tools"] == [_tool()]
    if effort is None:
        assert "reasoning_effort" not in seen
    else:
        assert seen["reasoning_effort"] == effort


def test_deepseek_v4_reasoning_defaults_are_explicit_and_bounded():
    payload = {}

    llm_core._apply_deepseek_v4_reasoning_defaults(
        payload,
        "https://api.deepseek.com/v1/chat/completions",
        "deepseek-v4-pro",
    )

    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "high"


def test_deepseek_reasoning_defaults_do_not_affect_other_hosts():
    payload = {}

    llm_core._apply_deepseek_v4_reasoning_defaults(
        payload,
        "https://openrouter.ai/api/v1/chat/completions",
        "deepseek-v4-pro",
    )

    assert payload == {}


def test_deepseek_v4_thinking_off_overrides_reasoning_defaults():
    payload = {}

    llm_core._apply_deepseek_v4_reasoning_defaults(
        payload,
        "https://api.deepseek.com/v1/chat/completions",
        "deepseek-v4-pro",
        "off",
    )

    assert payload["thinking"] == {"type": "disabled"}
    assert payload["reasoning_effort"] == "none"
