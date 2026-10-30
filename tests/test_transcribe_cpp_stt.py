"""Granite transcribe.cpp opt-in runtime and provenance regressions."""

import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from services.stt import granite, transcribe_cpp as native
from src.constants import GRANITE_STT_MODEL_ID, GRANITE_STT_REVISION


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    binary = tmp_path / "transcribe-cli"
    binary.write_text("stub binary")
    model = tmp_path / "multilingual.gguf"
    model.write_bytes(b"GGUF" + b"prepared_multilingual_weights")
    provenance = {
        "model_id": GRANITE_STT_MODEL_ID,
        "revision": GRANITE_STT_REVISION,
        "engine_revision": native.TRANSCRIBE_CPP_REVISION,
        "tokenizer": "bpe",
        "sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
    }
    Path(str(model) + ".provenance.json").write_text(json.dumps(provenance))
    monkeypatch.setenv("ODYSSEUS_TRANSCRIBE_CPP_BIN", str(binary))
    monkeypatch.setenv("ODYSSEUS_GRANITE_GGUF", str(model))
    monkeypatch.setattr(native.shutil, "which", lambda _: str(binary))
    return model, provenance


def test_cpp_runtime_selects_no_torch(monkeypatch, prepared):
    monkeypatch.setenv("ODYSSEUS_GRANITE_ENGINE", "transcribe.cpp")
    monkeypatch.setattr(granite, "shutil", SimpleNamespace(which=lambda _: "/usr/bin/ffmpeg"))
    assert granite.dependency_error() is None
    assert isinstance(granite.get_granite_stt(), native.TranscribeCppSTT)
    assert not granite.get_granite_stt().loaded
    monkeypatch.setenv("ODYSSEUS_GRANITE_ENGINE", "no-engine")
    assert "must be" in granite.dependency_error()


def test_cpp_configuration_rejects_unpinned_or_unsafe_model(prepared, monkeypatch):
    model, provenance = prepared
    for key, value in [
        ("revision", "18ca3c1de6cd092b5a30c39fb0f04550b38ed1a0"),
        ("model_id", GRANITE_STT_MODEL_ID + "-nc"),
        ("engine_revision", "a" * 40),
        ("tokenizer", "gpt2"),
        ("sha256", "not sha"),
    ]:
        manifest = {**provenance, key: value}
        Path(str(model) + ".provenance.json").write_text(json.dumps(manifest))
        with pytest.raises(ValueError, match="pinned multilingual"):
            native._configuration()
    Path(str(model) + ".provenance.json").write_text(json.dumps(provenance))
    monkeypatch.setenv("ODYSSEUS_TRANSCRIBE_CPP_BACKEND", "sh; evil")
    with pytest.raises(ValueError, match="BACKEND"):
        native._configuration()
    monkeypatch.delenv("ODYSSEUS_TRANSCRIBE_CPP_BACKEND")
    for invalid in ["0", "65", "bad"]:
        monkeypatch.setenv("ODYSSEUS_TRANSCRIBE_CPP_THREADS", invalid)
        with pytest.raises(ValueError, match="THREADS"):
            native._configuration()


def test_cpp_rejects_corrupt_gguf_even_with_matching_manifest(prepared):
    model, _ = prepared
    model.write_bytes(b"INVALID")
    with pytest.raises(ValueError, match="not GGUF"):
        native.TranscribeCppSTT()._check_model(model, hashlib.sha256(model.read_bytes()).hexdigest())


def test_cpp_detects_modified_gguf(prepared):
    model, provenance = prepared
    runtime = native.TranscribeCppSTT()
    runtime._check_model(model, provenance["sha256"])
    model.write_bytes(b"GGUF" + b"modified")
    with pytest.raises(ValueError, match="checksum mismatch"):
        runtime._check_model(model, provenance["sha256"])


def test_cpp_transcribes_pcm_with_hermetic_subprocess(prepared, monkeypatch):
    monkeypatch.setattr(granite, "_decode_audio", lambda *a: np.array([.5, -.25], dtype=np.float32))
    monkeypatch.setenv("SUPER_SECRET_SERVICE_TOKEN", "should_not_reach_child")
    observed = []

    def fake_run(argv, **kwargs):
        assert argv[:2] == [str(prepared[0].parent / "transcribe-cli"), "--model"]
        assert argv[argv.index("--timestamps") + 1] == "none"
        assert argv[argv.index("--backend") + 1] == "auto"
        assert "SUPER_SECRET_SERVICE_TOKEN" not in kwargs["env"]
        assert kwargs["env"]["HOME"] == kwargs["cwd"]
        assert kwargs["env"]["TMPDIR"] == kwargs["cwd"]
        assert kwargs["timeout"] == 300
        audio = Path(argv[-1]).read_bytes()
        assert audio[:4] == b"RIFF"
        assert struct.unpack_from("<H", audio, 20)[0] == 3
        assert struct.unpack_from("<I", audio, 24)[0] == 16000
        assert struct.unpack_from("<H", audio, 22)[0] == 1
        assert struct.unpack_from("<f", audio, 44)[0] == pytest.approx(.5)
        output = Path(argv[argv.index("--output") + 1])
        output.write_text("  German speech recognized.  ")
        observed.append(Path(kwargs["cwd"]))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(native.subprocess, "run", fake_run)
    service = native.TranscribeCppSTT()
    assert service.transcribe(b"dummy", "de") == "German speech recognized."
    assert service.transcribe(b"dummy") == "German speech recognized."
    assert len(observed) == 2
    assert all(not path.exists() for path in observed)


def test_cpp_refuses_invalid_audio_language_before_process(prepared, monkeypatch):
    monkeypatch.setattr(native.subprocess, "run", lambda *a, **k: pytest.fail("subprocess must not run"))
    with pytest.raises(ValueError, match="supports en"):
        native.TranscribeCppSTT().transcribe(b"data", "ro")


def test_cpp_handles_empty_audio_without_running_cli(prepared, monkeypatch):
    monkeypatch.setattr(granite, "_decode_audio", lambda *a: np.array([], dtype=np.float32))
    monkeypatch.setattr(native.subprocess, "run", lambda *a, **k: pytest.fail("no subprocess for silent audio"))
    assert native.TranscribeCppSTT().transcribe(b"data") == ""


@pytest.mark.parametrize("result_kind", ["exit", "timeout", "size", "utf8", "missing"])
def test_cpp_reports_runtime_failures(prepared, monkeypatch, result_kind):
    monkeypatch.setattr(granite, "_decode_audio", lambda *a: np.ones(160, dtype=np.float32))

    def fake_run(args, **kwargs):
        if result_kind == "timeout":
            raise subprocess.TimeoutExpired(args, 300)
        if result_kind == "size":
            Path(args[args.index("--output") + 1]).write_bytes(b"x" * (native._MAX_TEXT_BYTES + 1))
        if result_kind == "utf8":
            Path(args[args.index("--output") + 1]).write_bytes(b"\xff")
        return SimpleNamespace(returncode=2 if result_kind == "exit" else 0)

    monkeypatch.setattr(native.subprocess, "run", fake_run)
    with pytest.raises((RuntimeError, ValueError, UnicodeDecodeError)):
        native.TranscribeCppSTT().transcribe(b"audio")


def test_native_stats_and_service_route(monkeypatch, prepared):
    from services.stt.stt_service import STTService

    monkeypatch.setenv("ODYSSEUS_GRANITE_ENGINE", "transcribe.cpp")
    monkeypatch.setattr(granite, "shutil", SimpleNamespace(which=lambda _: "/usr/bin/ffmpeg"))
    monkeypatch.setattr(native, "get_transcribe_cpp_stt", lambda: SimpleNamespace(loaded=False, transcribe=lambda *a: "hello"))
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: {
        "stt_enabled": True, "stt_provider": "local",
        "stt_model": "granite-speech-5.0-470m-turboctc", "stt_language": "",
    })
    assert service.available
    assert service.get_stats()["engine"] == "transcribe.cpp"
    assert service.get_stats()["model_loaded"] is False
    assert service.transcribe(b"audio") == "hello"

@pytest.mark.skipif(not os.environ.get("ODYSSEUS_NATIVE_GRANITE_SMOKE_AUDIO"), reason="opt-in native real-model smoke")
def test_native_real_model_through_stt_route(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.stt_routes import setup_stt_routes
    from services.stt.stt_service import STTService

    wav = Path(os.environ["ODYSSEUS_NATIVE_GRANITE_SMOKE_AUDIO"])
    webm = tmp_path / "speech.webm"
    subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-i", str(wav), "-c:a", "libopus", str(webm)],
        check=True, timeout=30,
    )
    monkeypatch.setenv("ODYSSEUS_GRANITE_ENGINE", "transcribe.cpp")
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: {
        "stt_enabled": True, "stt_provider": "local",
        "stt_model": "granite-speech-5.0-470m-turboctc", "stt_language": "",
    })
    app = FastAPI()
    app.include_router(setup_stt_routes(service))
    with TestClient(app) as client:
        status = client.get("/api/stt/stats")
        assert status.status_code == 200
        assert status.json()["available"] is True
        assert status.json()["engine"] == "transcribe.cpp"
        response = client.post("/api/stt/transcribe", files={
            "file": ("speech.webm", webm.read_bytes(), "audio/webm"),
        })
        assert response.status_code == 200, response.text
        text = response.json()["text"].lower()
        assert "ask not" in text and "country" in text
        print("Native Granite real CPU transcription:", text)
