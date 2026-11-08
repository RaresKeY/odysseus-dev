#!/usr/bin/env python3
"""Verify generation and native-child cancellation on an owned test deployment.

Run this trusted deployment observer on Debian. Model commands execute only
through the real application API and its normal native containment.
"""
from __future__ import annotations
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import threading
import time
from types import SimpleNamespace
import uuid

spec = importlib.util.spec_from_file_location("stop_live", Path(__file__).with_name("fork_live_tests.py"))
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


class Turn:
    def __init__(self, client, session, prompt, endpoint, model):
        self.events = []
        self.finished = threading.Event()
        self.delta = threading.Event()
        self.active = threading.Event()
        self.error = None
        self.run_id = None
        def run():
            try:
                with client.response("/api/chat_stream", {"session": session, "message": prompt,
                    "mode": "agent", "allow_bash": "true", "allow_web_search": "false",
                    "use_web": "false", "use_rag": "false", "selected_endpoint_id": endpoint,
                    "selected_model": model}, form=True) as response:
                    self.run_id = response.getheader("X-Odysseus-Run-Id")
                    live.require(bool(self.run_id), "missing_stream_run_id")
                    total = 0
                    while True:
                        line = response.readline(65537)
                        if not line:
                            break
                        total += len(line)
                        live.require(len(line) <= 65536 and total <= live.MAX_BYTES, "stream_too_large")
                        if not line.startswith(b"data: "):
                            continue
                        raw = line[6:].strip()
                        if raw == b"[DONE]":
                            break
                        event = json.loads(raw)
                        self.events.append(event)
                        if event.get("type") in {"model_info", "model_actual", "turn_contract"}:
                            self.active.set()
                        if event.get("delta") and not event.get("thinking"):
                            self.delta.set()
            except Exception as exc:
                self.error = str(exc) if isinstance(exc, live.CheckFailure) else type(exc).__name__
            finally:
                self.finished.set()
        self.thread = threading.Thread(target=run, daemon=True)
        self.thread.start()


def observe(container, marker, watched=None):
    """Return task-owned namespace process identities without raw command lines."""
    code = r"""
from pathlib import Path
import json
marker = MARKER
watched = WATCHED
rows = {}
for path in Path('/proc').iterdir():
 if not path.name.isdigit():continue
 try:
  argv=(path/'cmdline').read_bytes().split(b'\0')
  stat=(path/'stat').read_text().split(') ',1)[1].split()
  rows[int(path.name)]={'parent':int(stat[1]),'start':stat[19],'name':argv[0].decode(errors='replace').rsplit('/',1)[-1] if argv else '', 'matches':marker.encode() in b' '.join(argv)}
 except (OSError,ValueError,IndexError):pass
owned={pid for pid,row in rows.items() if row['matches'] and row['name'] in ('bash','sh')}
while True:
 descendants={pid for pid,row in rows.items() if row['parent'] in owned}
 if descendants<=owned:break
 owned|=descendants
owned|={pid for pid,row in rows.items() if watched.get(str(pid))==row['start']}
print(json.dumps({'pids':sorted(owned),'identities':{str(pid):rows[pid]['start'] for pid in owned},'sleep_child':any(rows[pid]['name']=='sleep' for pid in owned)}))
""".replace("MARKER", json.dumps(marker)).replace("WATCHED", json.dumps(watched or {}))
    result = subprocess.run(["podman", "exec", "-i", container, "python3", "-I", "-"], input=code,
                            text=True, capture_output=True, timeout=10, check=True)
    return json.loads(result.stdout)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deployment", type=Path, required=True)
    parser.add_argument("--expected-source", required=True)
    parser.add_argument("--login-file", required=True)
    parser.add_argument("--endpoint-id", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--origin", default="http://127.0.0.1:7825")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    live.local_origin(args.origin)
    meta = json.loads(args.deployment.read_text())
    container = meta.get("name", "")
    live.require(bool(re.fullmatch(r"odysseus-debian-regression-fixture-[a-z0-9-]+", container)), "disposable_fixture_required")
    live.require(bool(re.fullmatch(r"[0-9a-f]{40}", args.expected_source)) and meta.get("source_commit") == args.expected_source, "wrong_fixture_source")
    inspected = json.loads(subprocess.check_output(["podman", "inspect", container], text=True))[0]
    live.require(inspected["State"]["Running"] and inspected["Config"]["Labels"].get("io.rareskey.source") == args.expected_source, "wrong_running_fixture")
    root = Path(meta["root"]).resolve()
    live.require(root.name == container and meta["socket"] == str(root / "runtime/odysseus.sock"), "fixture_socket_binding_mismatch")
    runtime_mount = next((m for m in inspected["Mounts"] if m["Destination"] == "/run/jev"), None)
    live.require(runtime_mount is not None and runtime_mount["Source"] == str(root / "runtime"), "fixture_runtime_mount_mismatch")
    client = live.Client(args.origin, meta["socket"], timeout=120)
    options = SimpleNamespace(endpoint_id=args.endpoint_id, model=args.model, manual_template=None)
    suite = live.Suite(client, live.load_login(args.login_file), options)
    suite.authenticate()
    phases = []
    marker = "debian-stop-" + uuid.uuid4().hex[:16]
    try:
        for native in (False, True):
            outcome = {"kind": "native_children" if native else "generation", "passed": False}
            if native and not phases[0]["passed"]:
                phases.append({**outcome, "reason": "generation_preflight_failed"})
                break
            turn = None
            try:
                with suite.session(manual=True) as sid:
                    try:
                        command = 'sleep 90 & child=$!; printf "%s\\n" "' + marker + '"; wait "$child"'
                        prompt = ("Execute this exact bash command once in the selected workspace. Do not use another tool: " + command
                                  if native else "Begin with " + marker + " then write the numbers from 1 through 5000 as plain text. Do not use any tools.")
                        turn = Turn(client, sid, prompt, args.endpoint_id, args.model)
                        observed = None
                        deadline = time.monotonic() + 75
                        while time.monotonic() < deadline:
                            if native:
                                observed = observe(container, marker)
                                if observed["sleep_child"]:
                                    break
                            elif turn.active.is_set() and not turn.finished.is_set():
                                time.sleep(1)
                                live.require(not turn.finished.is_set(), "turn_finished_before_stop_probe")
                                break
                            if turn.finished.is_set():
                                raise live.CheckFailure("turn_finished_before_stop_probe", {"transport_error": turn.error})
                            time.sleep(.2)
                        else:
                            raise live.CheckFailure("active_turn_not_observed")
                        if native:
                            live.require(any(e.get("type") == "tool_start" and e.get("tool") == "bash" and e.get("command") == command for e in turn.events), "native_command_not_exact")
                        ack = client.request("/api/chat/stop/" + sid, {}, run_id=turn.run_id)
                        live.require(ack.get("stopped") is True, "exact_run_stop_not_accepted")
                        live.require(turn.finished.wait(12), "stream_not_closed_after_stop")
                        if native:
                            deadline = time.monotonic() + 8
                            remaining = observe(container, marker, observed["identities"])
                            while remaining["pids"] and time.monotonic() < deadline:
                                time.sleep(.2)
                                remaining = observe(container, marker, observed["identities"])
                            live.require(not remaining["pids"], "native_child_survived_stop")
                            outcome.update(native_child_seen=True, namespace_process_identities=observed["identities"], all_task_processes_gone=True)
                        outcome.update(passed=True, stream_closed=True, exact_run_stop_accepted=True)
                    finally:
                        # Idempotent stop is cleanup; no model action is replayed.
                        if turn and turn.run_id:
                            client.request("/api/chat/stop/" + sid, {}, run_id=turn.run_id)
            except Exception as exc:
                outcome["reason"] = str(exc) if isinstance(exc, live.CheckFailure) else type(exc).__name__
                if isinstance(exc, live.CheckFailure):
                    outcome["diagnostics"] = exc.evidence
            phases.append(outcome)
    finally:
        client.request("/api/auth/logout", {})
    report = {"schema": "odysseus-debian-supplement/v1", "expected_source": args.expected_source,
              "results": [{"id": "CH-07", "status": "passed" if all(p["passed"] for p in phases) else "failed",
                           "profile": "real-native-stop", "evidence": {"phases": phases, "model": args.model, "endpoint_id": args.endpoint_id}}]}
    args.output.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    args.output.chmod(0o600)
    print(json.dumps(report))
    return int(any(r["status"] != "passed" for r in report["results"]))


if __name__ == "__main__":
    raise SystemExit(main())
