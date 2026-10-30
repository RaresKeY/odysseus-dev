#!/usr/bin/env python3
"""Validate a transformed decision fixture offline. Never calls a provider."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.decisions.observability import OWNER_CAPTURE_BYTES, validate_capture


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("capture", type=Path)
    args = parser.parse_args(argv)
    try:
        with args.capture.open("rb") as stream:
            data = stream.read(OWNER_CAPTURE_BYTES + 1)
        report = validate_capture(data)
    except Exception:
        # Do not echo malformed packets, filenames or embedded secrets.
        print(json.dumps({"valid": False, "error": "invalid_capture", "provider_calls": 0, "action_calls": 0}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
