"""Regression tests for ReDoS in agent_loop's `<think>...</think>` stripping.

CodeQL flagged `py/polynomial-redos` on the lazy `<think>.*?</think>` pattern
used in `src/agent_loop.py` (one compiled `_THINK_RE`, one inline copy). It is
applied with `re.sub` over a whole model response. When the closing delimiter
is missing, the engine rescans to end-of-string from every `<think>` opener ->
O(n^2) on attacker-influenced input (prompt injection via tool output /
retrieved content echoed back by the model).

The agent now delegates to the shared reasoning parser, including Gemma thought
channels and dangling reasoning. Ordinary closed <think> blocks retain their
visible output; adversarial opener floods must still complete promptly.
"""

import pytest
import time

from src.agent_loop import _strip_think_blocks

_BUDGET_S = 4.0


@pytest.mark.parametrize("text,visible", [
    ("", ""),
    ("no tags here at all", "no tags here at all"),
    ("<think>hidden</think>visible", "visible"),
    ("before<think>cot</think>after", "beforeafter"),
    ("a<think>one</think>b<think>two</think>c", "abc"),
    ("<think>only</think>", ""),
    ("<thinking>hidden</thinking>visible", "visible"),
    ('<thought source="gemma">hidden</thought>visible', "visible"),
    ("<|channel>thought\nhidden<channel|><|channel>response\nvisible<channel|>", "visible"),
    ("visible<think>unfinished", "visible"),
    ("prefilled reasoning</think>visible", "prefilled reasoning</think>visible"),
    ("CASE <THINK>UP</THINK> mix <Think>x</Think>", "CASE mix"),
])
def test_supported_reasoning_is_not_visible(text, visible):
    assert _strip_think_blocks(text) == visible


def test_empty_and_none_safe():
    assert _strip_think_blocks(None) == ""


# -- ReDoS bound -------------------------------------------------------------

@pytest.mark.parametrize("opener", ["<think>", "<thought>", '<thinking time="1">', "<|channel>thought\n"])
def test_many_openers_no_closer_is_linear(opener):
    # Attacker echoes thousands of "<think>" with no closer. The lazy regex
    # rescans to EOS from each opener (O(n^2)); the helper scans once.
    hostile = opener * 60_000 + "x"
    start = time.perf_counter()
    out = _strip_think_blocks(hostile)
    elapsed = time.perf_counter() - start
    # An unfinished reasoning block contains no visible answer.
    assert out == ""
    assert elapsed < _BUDGET_S, f"took {elapsed:.2f}s (expected linear)"


def test_openers_then_one_far_closer_is_linear():
    hostile = "<think>" * 60_000 + "</think>" + "tail"
    start = time.perf_counter()
    out = _strip_think_blocks(hostile)
    elapsed = time.perf_counter() - start
    # First opener pairs with the single closer; lazy match spans to it.
    assert out == "tail"
    assert elapsed < _BUDGET_S, f"took {elapsed:.2f}s (expected linear)"
