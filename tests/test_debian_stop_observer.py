"""The stop oracle must detect reparented children and reject reused PIDs."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import shutil
from types import SimpleNamespace

PATH = Path(__file__).resolve().parents[1] / "scripts/debian_stop_tests.py"
spec = importlib.util.spec_from_file_location("debian_stop", PATH)
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def test_observer_tracks_children_after_parent_exit_and_rejects_pid_reuse(tmp_path, monkeypatch):
    marker = "debian-stop-" + "a" * 16
    def process(pid, parent, name, start, text=""):
        path = tmp_path / str(pid)
        path.mkdir(exist_ok=True)
        (path / "cmdline").write_bytes(("/bin/" + name + "\0" + text).encode())
        fields = ["S", str(parent), *(["0"] * 17), str(start)]
        (path / "stat").write_text(str(pid) + " (" + name + ") " + " ".join(fields))
    def execute(arguments, **options):
        assert arguments == ["podman", "exec", "-i", "fixture", "python3", "-I", "-"]
        code = options["input"].replace("Path('/proc')", "Path(" + repr(str(tmp_path)) + ")")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exec(code, {})
        return SimpleNamespace(stdout=output.getvalue())
    monkeypatch.setattr(m.subprocess, "run", execute)
    process(100, 1, "bash", 500, marker)
    process(101, 100, "sleep", 501)
    before = m.observe("fixture", marker)
    assert before["sleep_child"] and before["pids"] == [100, 101]
    shutil.rmtree(tmp_path / "100")
    process(101, 1, "sleep", 501)
    orphan = m.observe("fixture", marker, before["identities"])
    assert orphan["pids"] == [101] and orphan["sleep_child"]
    process(101, 1, "sleep", 999)
    assert m.observe("fixture", marker, before["identities"])["pids"] == []
