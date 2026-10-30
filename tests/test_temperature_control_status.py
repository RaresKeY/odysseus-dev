"""The context control follows transport omission rules and route evidence."""
import pytest
from src import llm_core


@pytest.mark.parametrize("url,model,enabled", [
    ("https://api.openai.com/v1", "gpt-5", False),
    ("https://api.openai.com/v1", "gpt-4o", True),
    ("https://chatgpt.com/backend-api/codex", "gpt-4o", False),
    ("https://api.anthropic.com", "claude-opus-4-7", False),
    ("https://api.anthropic.com", "claude-sonnet-4-6", True),
    ("https://api.moonshot.ai/v1", "kimi-k2.6", False),
    ("https://local.invalid/v1", "kimi-k2.6", True),
    ("https://generativelanguage.googleapis.com/v1beta/openai", "gemini-2.5-flash", True),
    ("", "", False),
])
def test_temperature_follows_selected_transport(url, model, enabled):
    result = llm_core.temperature_control_status(url, model)
    assert result["enabled"] is enabled
    assert bool(result["reason"]) is (not enabled)


def test_exact_endpoint_evidence_can_omit_temperature(monkeypatch):
    seen = []
    class Controls:
        def permits(self, control, value):
            seen.append((control, value))
            return False
    monkeypatch.setattr(llm_core, "_request_controls_for", lambda *args, **kwargs: Controls())
    assert not llm_core.temperature_control_status("https://local.invalid/v1", "custom", temperature=0.7)["enabled"]
    assert seen == [("temperature", 0.7)]
