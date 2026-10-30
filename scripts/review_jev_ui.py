"""Temporary browser evidence collection for the fork integration review."""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
BASE = "2903de8089fd7a3a1d80fb0023cbcda6c2ad80ae"


def _run(arguments, *, timeout=180, env=None):
    return subprocess.run(arguments, cwd=ROOT, capture_output=True, text=True, timeout=timeout, env=env)


def _emit(directory):
    files = sorted(directory.glob("*.png"))
    files += [directory / name for name in (
        "candidate-baseline.json", "property-diff.json",
        "browser-review.json", "source-identity.json",
    )]
    for path in files:
        if not path.is_file():
            continue
        encoded = base64.b64encode(path.read_bytes()).decode("ascii")
        parts = [encoded[i:i + 4000] for i in range(0, len(encoded), 4000)]
        for index, part in enumerate(parts):
            print("JEV_UI_EVIDENCE=" + json.dumps({
                "name": path.name, "part": index, "parts": len(parts), "data": part,
            }), flush=True)


def capture_evidence(head_snapshot, candidate_baseline):
    with tempfile.TemporaryDirectory(prefix="jev-ui-review-") as temporary:
        directory = Path(temporary)
        artifacts = directory / "evidence"
        artifacts.mkdir()
        base = directory / "base"
        error = None
        (artifacts / "candidate-baseline.json").write_text(
            json.dumps(candidate_baseline, indent=2) + "\n"
        )
        try:
            if _run(["git", "cat-file", "-e", BASE + "^{commit}"]).returncode:
                fetched = _run(["git", "fetch", "--depth=1", "origin", BASE])
                assert fetched.returncode == 0, fetched.stderr
            checkout = _run(["git", "worktree", "add", "--detach", str(base), BASE])
            assert checkout.returncode == 0, checkout.stderr
            (base / "node_modules").symlink_to(ROOT / "node_modules", target_is_directory=True)
            base_env = os.environ.copy()
            base_env.pop("ODYSSEUS_TEST_STATIC_ORIGIN", None)
            captured = _run([
                sys.executable, str(base / "scripts/css_snapshot.py"),
                "--dump", str(artifacts / "base-computed.json"),
            ], env=base_env, timeout=960)
            assert (artifacts / "base-computed.json").is_file(), captured.stdout + captured.stderr
            before = json.loads((artifacts / "base-computed.json").read_text())
            changes = []
            for page in sorted(set(before) | set(head_snapshot)):
                for variant in sorted(set(before.get(page, {})) | set(head_snapshot.get(page, {}))):
                    old = before.get(page, {}).get(variant, {})
                    new = head_snapshot.get(page, {}).get(variant, {})
                    for element in sorted(set(old) | set(new)):
                        a, b = old.get(element, {}), new.get(element, {})
                        for prop in sorted(set(a) | set(b)):
                            if a.get(prop) != b.get(prop):
                                changes.append({
                                    "page": page, "variant": variant, "element": element,
                                    "property": prop, "before": a.get(prop), "after": b.get(prop),
                                })
            (artifacts / "property-diff.json").write_text(json.dumps(changes, indent=2) + "\n")
            (artifacts / "source-identity.json").write_text(json.dumps({
                "base": BASE,
                "tested_merge": _run(["git", "rev-parse", "HEAD"]).stdout.strip(),
                "base_snapshot_exit": captured.returncode,
                "base_snapshot_output": captured.stdout[-3000:],
            }, indent=2) + "\n")
            result = _run([
                "node", str(ROOT / "scripts/review_jev_ui.mjs"),
                str(base), str(ROOT), str(artifacts),
            ])
            print("JEV_BROWSER_RESULT=" + result.stdout[-4000:], flush=True)
            assert result.returncode == 0, result.stderr + result.stdout
        except Exception as exc:
            error = exc
        finally:
            _emit(artifacts)
            if base.exists():
                _run(["git", "worktree", "remove", "--force", str(base)])
        if error is not None:
            raise error
