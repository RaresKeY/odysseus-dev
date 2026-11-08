#!/usr/bin/env python3
"""Boot an isolated source/state copy through the normal dev launcher and smoke it.

Run in the prepared secretless runtime with networking disabled. The existing
smoke suite provides its deterministic model and email fixtures; no real account
or provider credential belongs in this profile.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[0-9a-f]{40}", args.expected_source):
        parser.error("expected-source must be a pinned source SHA")
    # The caller supplies a verified immutable source export. Application startup
    # and tests use the same launcher/state paths as ordinary development.
    with tempfile.TemporaryDirectory(prefix="odysseus-debian-smoke-") as tmp:
        work = Path(tmp)
        source = work / "source"
        shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", ".odysseus-dev", "__pycache__", ".pytest_cache", "venv", "data", "logs", ".env", ".env.*"))
        subprocess.run(["git", "init", "--quiet", str(source)], check=True, capture_output=True)
        interpreter = work / "interpreter/bin/python"
        interpreter.parent.mkdir(parents=True)
        interpreter.symlink_to(sys.executable)
        env = dict(os.environ)
        env.update({"ODYSSEUS_EMAIL_FIXTURE": "1", "ODYSSEUS_DISABLE_MCP": "1",
                    "ODYSSEUS_STARTUP_WARMUPS": "0", "ODYSSEUS_MODEL_KEEPALIVE": "0",
                    "ODYSSEUS_INPROCESS_TASKS": "0", "ODYSSEUS_INPROCESS_POLLERS": "0",
                    "ODYSSEUS_TASK_AUTOMATION_ENABLED": "0", "ODYSSEUS_SOURCE_COMMIT": args.expected_source})
        xml = work / "smoke.xml"
        logs = []
        def invoke(command):
            result = subprocess.run(command, cwd=source, env=env, text=True,
                                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=360)
            logs.append(result.stdout)
            return result
        dev = [sys.executable, str(source / "scripts/odysseus-dev")]
        result = None
        try:
            boot = invoke(dev + ["up", "--no-chroma", "--venv", str(interpreter.parent.parent)])
            if boot.returncode == 0:
                result = invoke([sys.executable, str(source / "scripts/odysseus-smoke"), "--no-boot", "--venv", str(interpreter.parent.parent), "--", "--junitxml=" + str(xml)])
        finally:
            invoke(dev + ["down"])
        tests = []
        if xml.exists():
            for case in ET.parse(xml).iter("testcase"):
                status = ("failed" if case.find("failure") is not None or case.find("error") is not None
                          else "skipped" if case.find("skipped") is not None else "passed")
                tests.append({"name": case.get("name"), "module": case.get("classname"), "status": status})
        report = {"schema": "odysseus-debian-smoke/v1", "expected_source": args.expected_source,
                  "status": "passed" if result is not None and result.returncode == 0 and tests else "failed",
                  "tests": tests, "boot_exit": boot.returncode,
                  "test_exit": None if result is None else result.returncode,
                  "profile": "synthetic-model-and-email; vectors disabled; secretless runtime"}
        args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        args.output.chmod(0o600)
        print(json.dumps(report), flush=True)
        if report["status"] != "passed":
            # This profile contains only generated synthetic credentials. Keep
            # diagnostic logs private and never put raw logs in the report.
            args.output.with_suffix(".log").write_text("\n".join(logs))
            args.output.with_suffix(".log").chmod(0o600)
            print("smoke_diagnostics: " + str(args.output.with_suffix(".log")), flush=True)
        return 0 if report["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
