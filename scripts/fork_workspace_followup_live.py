#!/usr/bin/env python3
"""Check a natural-language clarification followed by a workspace file write.

Credentials arrive on stdin. The report records the unique scratch filename and
readback hash for operator cleanup; this probe never deletes existing files.
"""
import argparse
import json
from pathlib import Path
import sys
import urllib.parse
import uuid

from fork_live_tests import Client, Suite, CheckFailure, read_events, require


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--endpoint-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--manual-template", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    login = json.load(sys.stdin)
    client = Client("http://127.0.0.1:7824", timeout=180)
    suite = Suite(client, {k: login[k] for k in ("username", "password")}, args)
    name = "codex_workspace_followup_" + uuid.uuid4().hex[:12] + ".txt"
    report = {"case": "FS-15", "source": args.expected_source,
              "model": args.model, "scratch_name": name, "checks": {}, "turns": []}
    try:
        suite.runtime()
        before = client.request("/api/chat-settings")
        workspace = client.request("/api/workspace/default")["path"]
        entries = client.request("/api/workspace/files?" + urllib.parse.urlencode({"workspace": workspace}))
        require(not any(e["name"] == name for e in entries["entries"]), "scratch_already_exists")
        with suite.session(manual=True) as sid:
            report["temporary_session"] = sid
            require(client.request("/api/session/" + sid + "/automation")["approval_mode"]
                    == "auto_approve", "not_auto_approve")
            messages = [
                "Make a file named " + name + " in the selected workspace and write in it. "
                "Before doing anything, ask me what content to use in one ordinary text question. "
                "Do not call tools in this first response. Only this unique test file belongs to this task.",
                "anything",
            ]
            for index, message in enumerate(messages):
                with client.response("/api/chat_stream", {
                    "session": sid, "message": message, "mode": "agent", "workspace": workspace,
                    "allow_bash": "true", "allow_web_search": "false", "use_web": "false",
                    "use_rag": "false", "selected_endpoint_id": args.endpoint_id,
                    "selected_model": args.model}, form=True) as response:
                    events = read_events(response)
                receipts = [e for e in events if e.get("type") == "tool_output"]
                report["turns"].append({
                    "receipts": [{k: e.get(k) for k in (
                        "tool", "exit_code", "execution_attempted", "blocked", "failure_kind")}
                        for e in receipts],
                    "error_statuses": [e.get("status") for e in events if e.get("type") == "error"],
                })
                text = "".join(str(e.get("delta", "")) + str(e.get("response", ""))
                               for e in events)
                if index == 0:
                    require(not receipts and "?" in text, "clarification_not_reproduced")
                    report["checks"]["natural_question"] = True
                else:
                    require(not any(e.get("blocked") for e in receipts), "action_blocked")
                    report["checks"]["workspace_write_receipt"] = any(
                        e.get("tool") in {"write_file", "bash", "python", "apply_patch"}
                        and e.get("execution_attempted") is True and e.get("exit_code") == 0
                        for e in receipts)
                    report["checks"]["final_response_completed"] = (
                        "task is incomplete" not in text.lower()
                        and not any(e.get("type") == "error" for e in events))
        report["checks"]["temporary_chat_removed"] = True
        report["checks"]["owner_defaults_preserved"] = before == client.request("/api/chat-settings")
        require(all(report["checks"].values()), "followup_check_failed")
        report["status"] = "passed"
    except Exception as exc:
        report["status"] = "failed"
        report["reason"] = str(exc) if isinstance(exc, CheckFailure) else type(exc).__name__
        if isinstance(exc, CheckFailure):
            report["failure_evidence"] = exc.evidence
    finally:
        if client.cookies:
            try:
                workspace = client.request("/api/workspace/default")["path"]
                file = client.request("/api/workspace/file?" + urllib.parse.urlencode(
                    {"workspace": workspace, "path": name}))
                report["revision"] = file["revision"]
                report["file_size"] = file["size"]
                report["checks"]["nonempty_file_readback"] = bool(file["content"].strip())
            except Exception:
                report["checks"]["nonempty_file_readback"] = False
            try:
                client.request("/api/auth/logout", {})
            except Exception:
                report["checks"]["logout"] = False
        if not all(report["checks"].values()):
            report["status"] = "failed"
        args.output.write_text(json.dumps(report, indent=2) + "\n")
        args.output.chmod(0o600)
        print(json.dumps(report))
    return 0 if report.get("status") == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
