import pytest

from src.agent_loop import _compute_final_metrics, _usage_bucket, _usage_bucket_summary
from src.model_pricing import estimate_cost_usd


def _bucket(cache=None, source="real", model="gpt-5"):
    return _usage_bucket(
        round_num=1, model=model, endpoint_id=7, endpoint_label="Fixture",
        endpoint_cost_tracked=True, input_tokens=100, output_tokens=5,
        usage_source=source, cache_usage=cache,
    )


def test_buckets_keep_cache_counts_with_route_identity():
    bucket = _bucket({"cache_read_input_tokens": 80, "cache_creation_input_tokens": 10})
    assert bucket["endpoint_id"] == 7
    assert bucket["cache_read_input_tokens"] == 80
    assert bucket["cache_creation_input_tokens"] == 10
    summary = _usage_bucket_summary([bucket, bucket])
    assert summary["input_tokens"] == 200
    assert summary["cache_read_input_tokens"] == 160
    assert summary["cache_creation_input_tokens"] == 20


def test_mixed_or_estimated_rounds_do_not_claim_complete_cache_coverage():
    hit = _bucket({"cache_read_input_tokens": 80})
    estimated = _bucket({"cache_read_input_tokens": 80}, source="estimated")
    assert "cache_read_input_tokens" not in estimated
    summary = _usage_bucket_summary([hit, estimated])
    assert "cache_read_input_tokens" not in summary
    assert summary["usage_buckets"][0]["cache_read_input_tokens"] == 80
    assert summary["usage_source"] == "mixed"


def _metrics(buckets, cost=0):
    return _compute_final_metrics(
        messages=[], full_response="hello", total_duration=1,
        time_to_first_token=0.1, context_length=4096,
        real_input_tokens=200, real_output_tokens=10, has_real_usage=True,
        tool_events=[], round_texts=[], model="gpt-5",
        endpoint_url="https://api.openai.com/v1", real_cost_usd=cost,
        usage_buckets=buckets,
    )


def test_final_metrics_preserve_cache_counts_but_do_not_guess_cache_prices():
    metrics = _metrics([_bucket({"cache_read_input_tokens": 80})] * 2)
    assert metrics["cache_read_input_tokens"] == 160
    assert "cost_usd" not in metrics
    assert "cost_source" not in metrics


def test_reported_cost_still_wins_for_cached_requests():
    metrics = _metrics([_bucket({"cache_read_input_tokens": 80})], cost=0.001)
    assert metrics["cost_usd"] == pytest.approx(0.001)
    assert metrics["cost_source"] == "reported"


def test_cached_route_suppresses_estimate_even_when_other_route_is_unknown():
    metrics = _metrics([_bucket({"cache_read_input_tokens": 80}), _bucket()])
    assert "cache_read_input_tokens" not in metrics
    assert "cost_usd" not in metrics


@pytest.mark.parametrize("cache", [
    {"cache_read_input_tokens": 80}, {"cache_creation_input_tokens": 80},
    {"usage_buckets": [{"cache_read_input_tokens": 80}, {}]},
])
def test_estimator_rejects_known_cache_usage(cache):
    assert estimate_cost_usd("gpt-5", 100, 5, "https://api.openai.com/v1", cache_usage=cache) is None


def test_estimator_keeps_existing_behavior_without_cache_hits():
    args = ("gpt-5", 100, 5, "https://api.openai.com/v1")
    assert estimate_cost_usd(*args, cache_usage={"cache_read_input_tokens": 0}) == estimate_cost_usd(*args)
