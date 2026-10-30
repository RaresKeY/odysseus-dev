import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import io
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import time
from types import SimpleNamespace
from unittest.mock import Mock
import wave

import pytest

from services.stt import granite
from services.stt.stt_service import STTService
from src.constants import GRANITE_STT_MODEL, GRANITE_STT_MODEL_ID, GRANITE_STT_REVISION


def wav_bytes(seconds=0.1, rate=8000, channels=2):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(struct.pack("<h", 2000) * int(seconds * rate) * channels)
    return output.getvalue()


@pytest.fixture
def fake_runtime(monkeypatch):
    import numpy as np

    inputs = {"input_features": object(), "attention_mask": object()}

    class Inputs(dict):
        def to(self, device, dtype):
            return self

    processor = Mock(return_value=Inputs(inputs))
    processor.batch_decode.return_value = [" Recognized speech. "]
    model = Mock(device="cpu", dtype="float32")
    model.to.return_value = model
    model.eval.return_value = model
    auto_processor = Mock()
    auto_processor.from_pretrained.return_value = processor
    auto_model = Mock()
    auto_model.from_pretrained.return_value = model
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: False), float32="float32", bfloat16="bfloat16",
        inference_mode=nullcontext,
    ))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(
        AutoProcessor=auto_processor, AutoModelForCTC=auto_model,
    ))
    monkeypatch.setattr(granite, "dependency_error", lambda: None)
    monkeypatch.setattr(granite, "_decode_audio", lambda *args: np.ones(1600, dtype=np.float32))
    return SimpleNamespace(processor=processor, model=model, auto_processor=auto_processor, auto_model=auto_model)


@pytest.mark.parametrize("model", [GRANITE_STT_MODEL, GRANITE_STT_MODEL_ID])
def test_granite_model_aliases(model):
    assert granite.is_granite_model(model)


@pytest.mark.parametrize("model", ["base", GRANITE_STT_MODEL_ID + "-nc", "other/model", "", None])
def test_granite_rejects_unregistered_models(model):
    assert not granite.is_granite_model(model)


def test_granite_lazy_pinned_safetensors_load_once(fake_runtime):
    runtime = granite.GraniteSTT()
    assert not runtime.loaded
    assert not fake_runtime.auto_model.from_pretrained.called
    assert runtime.transcribe(b"audio") == "Recognized speech."
    assert runtime.transcribe(b"audio", "fr") == "Recognized speech."
    assert runtime.loaded
    fake_runtime.auto_processor.from_pretrained.assert_called_once_with(
        GRANITE_STT_MODEL_ID, revision=GRANITE_STT_REVISION, trust_remote_code=False,
    )
    fake_runtime.auto_model.from_pretrained.assert_called_once_with(
        GRANITE_STT_MODEL_ID, revision=GRANITE_STT_REVISION, trust_remote_code=False,
        dtype="float32", use_safetensors=True,
    )
    assert fake_runtime.processor.call_args.kwargs["sampling_rate"] == 16000
    assert fake_runtime.processor.call_args.kwargs["return_tensors"] == "pt"
    assert "language" not in fake_runtime.model.generate.call_args.kwargs
    assert fake_runtime.processor.batch_decode.call_args.kwargs == {"skip_special_tokens": True}


def test_granite_serializes_first_load(fake_runtime):
    runtime = granite.GraniteSTT()
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(runtime.transcribe, [b"one", b"two"])) == ["Recognized speech."] * 2
    assert fake_runtime.auto_model.from_pretrained.call_count == 1


def test_granite_failed_load_is_retryable(fake_runtime):
    runtime = granite.GraniteSTT()
    fake_runtime.auto_model.from_pretrained.side_effect = RuntimeError("load failed")
    with pytest.raises(RuntimeError, match="load failed"):
        runtime.transcribe(b"audio")
    assert not runtime.loaded
    assert runtime._processor is None
    fake_runtime.auto_model.from_pretrained.side_effect = None
    assert runtime.transcribe(b"audio") == "Recognized speech."


def test_granite_empty_audio_does_not_load_weights(fake_runtime, monkeypatch):
    import numpy as np

    monkeypatch.setattr(granite, "_decode_audio", lambda *args: np.array([], dtype=np.float32))
    assert granite.GraniteSTT().transcribe(b"audio") == ""
    assert not fake_runtime.auto_model.from_pretrained.called


def test_granite_rejects_unsupported_language_before_loading():
    with pytest.raises(ValueError, match="supports en"):
        granite.GraniteSTT().transcribe(b"audio", "ro")


@pytest.mark.parametrize("transformers_version,torch_version", [("5.15.0", "2.9.0"), ("5.16.0", "2.5.0")])
def test_granite_old_dependencies_explain_install(monkeypatch, transformers_version, torch_version):
    monkeypatch.setattr(granite.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(granite.importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr(granite, "version", lambda name: transformers_version if name == "transformers" else torch_version)
    assert "requirements-stt-granite.txt" in granite.dependency_error()


def test_granite_missing_dependencies_do_not_import_them(monkeypatch):
    monkeypatch.setattr(granite.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    monkeypatch.setattr(granite.importlib.util, "find_spec", lambda name: None)
    assert "requirements-stt-granite.txt" in granite.dependency_error()


def test_granite_missing_ffmpeg(monkeypatch):
    monkeypatch.setattr(granite.shutil, "which", lambda name: None)
    assert granite.dependency_error() == "Granite STT requires FFmpeg on PATH"


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg unavailable")
def test_granite_decodes_stereo_to_16khz_mono_and_cleans_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(granite.tempfile, "tempdir", str(tmp_path))
    samples = granite._decode_audio(wav_bytes())
    assert samples.shape == (1600,)
    assert samples.dtype.name == "float32"
    assert samples.mean() > 0
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg unavailable")
def test_granite_decode_failure_cleans_upload(tmp_path, monkeypatch):
    monkeypatch.setattr(granite.tempfile, "tempdir", str(tmp_path))
    with pytest.raises(ValueError, match="could not decode"):
        granite._decode_audio(b"not audio")
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg unavailable")
def test_granite_rejects_playlist_input():
    with pytest.raises(ValueError, match="could not decode"):
        granite._decode_audio(b"#EXTM3U\n#EXTINF:1,\nhttps://example.invalid/audio.wav\n")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg unavailable")
def test_granite_audio_cap_rejects_instead_of_truncating(tmp_path, monkeypatch):
    monkeypatch.setattr(granite, "GRANITE_STT_MAX_AUDIO_SECONDS", 1)
    source = tmp_path / "source.wav"
    source.write_bytes(wav_bytes(seconds=2))
    with pytest.raises(ValueError, match="at most 1 seconds"):
        granite._decode_audio(source)
    assert len(granite._decode_audio(source, start=1, end=1.5)) == 8000


@pytest.mark.parametrize("start,end", [(float("nan"), None), (float("inf"), None), (-1, None), (0, float("inf")), (1, 0), (0, 301)])
def test_granite_rejects_invalid_ranges(start, end):
    with pytest.raises(ValueError):
        granite._decode_audio(b"audio", start, end)


def configure_service(monkeypatch, enabled=True):
    service = STTService()
    monkeypatch.setattr(service, "_load_settings", lambda: {
        "stt_enabled": enabled, "stt_provider": "local",
        "stt_model": GRANITE_STT_MODEL, "stt_language": "",
    })
    monkeypatch.setattr(service, "_get_whisper", Mock(side_effect=AssertionError("Whisper must not load")))
    return service


def test_granite_service_dispatch_and_stats(monkeypatch):
    from services.stt import stt_service

    service = configure_service(monkeypatch)
    runtime = Mock(loaded=False)
    runtime.transcribe.return_value = "Hello."
    monkeypatch.setattr(stt_service, "get_granite_stt", lambda: runtime)
    monkeypatch.setattr(stt_service, "dependency_error", lambda: None)
    assert service.available
    assert service.get_stats()["model_loaded"] is False
    assert service.get_stats()["revision"] == GRANITE_STT_REVISION
    assert not runtime.transcribe.called
    assert service.transcribe(b"audio") == "Hello."
    runtime.transcribe.assert_called_once_with(b"audio", "")


def test_granite_disabled_stats_do_not_load_models(monkeypatch):
    from services.stt import stt_service

    service = configure_service(monkeypatch, enabled=False)
    monkeypatch.setattr(stt_service, "dependency_error", Mock(side_effect=AssertionError("disabled")))
    assert service.transcribe(b"audio") is None
    assert service.get_stats()["provider"] == "disabled"
    assert not service.available


def test_granite_unavailable_stats_include_install_help(monkeypatch):
    from services.stt import stt_service

    service = configure_service(monkeypatch)
    monkeypatch.setattr(stt_service, "dependency_error", lambda: "Install optional Granite dependencies")
    assert not service.available
    assert service.get_stats()["error"] == "Install optional Granite dependencies"


def test_stt_routes_run_model_work_outside_event_loop():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.stt_routes import setup_stt_routes

    def assert_worker():
        with pytest.raises(RuntimeError, match="no running event loop"):
            asyncio.get_running_loop()

    class Service:
        @property
        def available(self):
            assert_worker()
            return True

        def transcribe(self, audio):
            assert_worker()
            assert audio == b"test audio"
            return "Hello."

        def get_stats(self):
            assert_worker()
            return {"available": True}

    app = FastAPI()
    app.include_router(setup_stt_routes(Service()))
    with TestClient(app) as client:
        assert client.get("/api/stt/stats").json() == {"available": True}
        response = client.post("/api/stt/transcribe", files={"file": ("audio.webm", b"test audio", "audio/webm")})
        assert response.status_code == 200
        assert response.json() == {"text": "Hello."}


def run_media(tmp_path, monkeypatch, **kwargs):
    from src.agent_tools import media_tools
    from src.tool_execution import _active_workspace

    source = tmp_path / "source.wav"
    source.write_bytes(b"fixture audio")
    runtime = Mock()
    runtime.transcribe.return_value = "Bonjour."
    monkeypatch.setattr(media_tools, "get_granite_stt", lambda: runtime)
    token = _active_workspace.set(str(tmp_path))
    try:
        result = asyncio.run(media_tools.TranscribeMediaTool().execute(json.dumps({
            "path": "/workspace/source.wav", "model": GRANITE_STT_MODEL, **kwargs,
        }), {}))
    finally:
        _active_workspace.reset(token)
    return result, runtime


def test_granite_media_writes_plain_text_without_invented_timestamps(tmp_path, monkeypatch):
    result, runtime = run_media(tmp_path, monkeypatch, start="1", end="3", language="fr")
    assert result["exit_code"] == 0
    assert result["transcript"] == "Bonjour."
    assert result["timestamps"] is False
    assert result["segments"] is None
    assert result["language"] == "unknown"
    artifact = tmp_path / result["output_path"].removeprefix("/workspace/")
    assert artifact.read_text() == "Bonjour.\n"
    runtime.transcribe.assert_called_once_with(tmp_path / "source.wav", "fr", start=1.0, end=3.0)


@pytest.mark.parametrize("extension", ["srt", "vtt", "jsonl"])
def test_granite_media_rejects_timestamp_formats(tmp_path, monkeypatch, extension):
    result, runtime = run_media(tmp_path, monkeypatch, output_path=f"/workspace/out.{extension}")
    assert result["exit_code"] == 1
    assert "no timestamps" in result["error"]
    assert not runtime.transcribe.called
    assert not (tmp_path / f"out.{extension}").exists()


def test_granite_media_rejects_forced_language(tmp_path, monkeypatch):
    result, runtime = run_media(tmp_path, monkeypatch, language="en", force_language=True)
    assert result["exit_code"] == 1
    assert "cannot force" in result["error"]
    assert not runtime.transcribe.called


def test_granite_model_is_advertised_by_tool_schema():
    from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    function = next(item["function"] for item in FUNCTION_TOOL_SCHEMAS if item["function"]["name"] == "transcribe_media")
    assert GRANITE_STT_MODEL in function["parameters"]["properties"]["model"]["enum"]
    assert "without timestamps" in function["description"]
    assert "Granite TurboCTC" in BUILTIN_TOOL_DESCRIPTIONS["transcribe_media"]
    assert "plain text without timestamps" in BUILTIN_TOOL_DESCRIPTIONS["transcribe_media"]

    from src.clean_agent_preview import compact_schemas

    compact = next(item['function'] for item in compact_schemas(FUNCTION_TOOL_SCHEMAS)
                   if item['function']['name'] == 'transcribe_media')
    assert GRANITE_STT_MODEL in compact['parameters']['properties']['model']['enum']
    assert 'plain text without timestamps' in compact['description']
    assert 'forced-language' in compact['description']
    assert 'five-minute limit' in compact['description']
    assert 'Granite supports .txt only' in compact['parameters']['properties']['output_path']['description']


@pytest.mark.skipif(not os.environ.get("ODYSSEUS_GRANITE_SMOKE_AUDIO"), reason="opt-in real model download")
def test_granite_real_cpu_transcription_through_route(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from routes.stt_routes import setup_stt_routes

    source = Path(os.environ["ODYSSEUS_GRANITE_SMOKE_AUDIO"])
    webm = tmp_path / "speech.webm"
    granite.subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-i", str(source),
        "-c:a", "libopus", str(webm),
    ], check=True, timeout=30)
    service = configure_service(monkeypatch)
    app = FastAPI()
    app.include_router(setup_stt_routes(service))
    with TestClient(app) as client:
        before = client.get("/api/stt/stats")
        assert before.status_code == 200
        assert before.json()["available"] is True
        assert before.json()["model_loaded"] is False
        response = client.post("/api/stt/transcribe", files={
            "file": ("speech.webm", webm.read_bytes(), "audio/webm"),
        })
        assert response.status_code == 200, response.text
        text = response.json()["text"]
        assert "ask not" in text.lower()
        assert "country" in text.lower()
        assert client.get("/api/stt/stats").json()["model_loaded"] is True
        print("Granite real CPU transcription:", text)


def test_threaded_stt_only_loads_whisper_once(monkeypatch):
    service = STTService()
    loaded = object()
    calls = []

    def load(*args, **kwargs):
        calls.append((args, kwargs))
        time.sleep(0.02)
        return loaded

    monkeypatch.setattr(service, "_load_settings", lambda: {"stt_model": "base"})
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=load))
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=SimpleNamespace(is_available=lambda: False)))
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: service._get_whisper(), range(2)))
    assert results == [loaded, loaded]
    assert len(calls) == 1
