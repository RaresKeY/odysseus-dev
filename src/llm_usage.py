import math


CACHE_TOKEN_FIELDS = ("cache_read_input_tokens", "cache_creation_input_tokens")


def _token_count(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        return None
    count = int(value)
    return count if 0 <= count <= 2**63 - 1 else None


def normalize_usage_counts(input_value=0, output_value=0, *, cache_usage=None):
    input_tokens = _token_count(input_value)
    output_tokens = _token_count(output_value)
    if input_tokens is None or output_tokens is None:
        return None
    result = {"input_tokens": input_tokens, "output_tokens": output_tokens}
    if isinstance(cache_usage, dict):
        cache = {
            key: count for key in CACHE_TOKEN_FIELDS
            if key in cache_usage and (count := _token_count(cache_usage[key])) is not None
        }
        if sum(cache.values()) <= input_tokens:
            result.update(cache)
    return result


def openai_cache_usage(usage, timings=None):
    for name in ("prompt_tokens_details", "input_tokens_details"):
        details = usage.get(name)
        if isinstance(details, dict) and "cached_tokens" in details:
            return {"cache_read_input_tokens": details["cached_tokens"]}
    if isinstance(timings, dict) and "cache_n" in timings:
        return {"cache_read_input_tokens": timings["cache_n"]}
    return {}


def normalize_anthropic_usage(usage):
    counts = [_token_count(usage.get(key, 0)) for key in ("input_tokens", *CACHE_TOKEN_FIELDS)]
    if any(count is None for count in counts):
        return None
    return normalize_usage_counts(
        sum(counts), usage.get("output_tokens", 0), cache_usage=usage,
    )


def aggregate_cache_usage(usages):
    if not usages:
        return {}
    result = {}
    for key in CACHE_TOKEN_FIELDS:
        counts = [_token_count(usage.get(key)) for usage in usages]
        if all(count is not None for count in counts):
            total = _token_count(sum(counts))
            if total is not None:
                result[key] = total
    return result


def has_cache_usage(usage):
    if not isinstance(usage, dict):
        return False
    buckets = usage.get("usage_buckets")
    records = [usage] + (buckets if isinstance(buckets, list) else [])
    return any(
        (_token_count(record.get(key)) or 0) > 0
        for record in records if isinstance(record, dict)
        for key in CACHE_TOKEN_FIELDS
    )
