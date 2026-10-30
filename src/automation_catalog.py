"""Compact, exact offered membership under a hard UTF-8 decision budget.

Adapted from earlier Jev concern-series catalog/budget corrections. Catalog
membership is selection evidence only; every selected operation still passes
the canonical permission/resource/approval dispatcher.
"""
from __future__ import annotations

import copy
import json
import math


class CatalogError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _json_bytes(value):
    def check(item, depth=0):
        if depth > 32:
            raise CatalogError('automation_catalog_invalid_json')
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) is list:
            for child in item:
                check(child, depth + 1)
            return
        if type(item) is dict and all(type(key) is str for key in item):
            for child in item.values():
                check(child, depth + 1)
            return
        raise CatalogError('automation_catalog_invalid_json')
    try:
        check(value)
        return json.dumps(value, ensure_ascii=False, allow_nan=False,
                          separators=(',', ':')).encode('utf-8')
    except (TypeError, ValueError, OverflowError, RecursionError, UnicodeError):
        raise CatalogError('automation_catalog_invalid_json') from None


def compact_catalog(state, descriptions, offered, *, max_request_bytes=12_000,
                    max_candidates=16):
    """Freeze the exact current candidate table, refusing silent loss/truncation.

    Control choices may map to None. Concrete choices map to inert operation
    dictionaries. No operation absent from the displayed descriptions can be
    smuggled into the dispatch table, nor can a description name an absent op.
    The request budget covers the actual compact state/choice wire projection.
    """
    if (type(max_request_bytes) is not int or not 1 <= max_request_bytes <= 60 * 1024
            or type(max_candidates) is not int or not 2 <= max_candidates <= 128):
        raise CatalogError('automation_catalog_invalid_budget')
    if (type(state) is not dict or type(descriptions) is not dict or type(offered) is not dict
            or set(descriptions) != set(offered)
            or not 2 <= len(descriptions) <= max_candidates
            or any(type(key) is not str or not key or len(key) > 160
                   or type(text) is not str or not text or len(text) > 2000
                   for key, text in descriptions.items())
            or any(value is not None and type(value) is not dict for value in offered.values())):
        raise CatalogError('automation_catalog_invalid_membership')
    # Validate inert operations too, even though only their descriptions/state
    # are sent for ranking. Live handles and non-JSON objects never belong here.
    _json_bytes(offered)
    encoded = _json_bytes({'state': state, 'choices': descriptions})
    if len(encoded) > max_request_bytes:
        raise CatalogError('automation_catalog_request_oversized')
    return copy.deepcopy(state), copy.deepcopy(descriptions), copy.deepcopy(offered)
