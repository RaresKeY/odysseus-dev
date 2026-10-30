"""Optional, local Granite Speech 5.0 TurboCTC inference."""

from __future__ import annotations

import importlib.util
from importlib.metadata import PackageNotFoundError, version
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading

from src.constants import (
    GRANITE_STT_LANGUAGES,
    GRANITE_STT_MAX_AUDIO_SECONDS,
    GRANITE_STT_MODEL,
    GRANITE_STT_MODEL_ID,
    GRANITE_STT_REVISION,
    GRANITE_STT_SAMPLE_RATE,
)

_INSTALL_HELP = "Install optional Granite STT dependencies: pip install -r requirements-stt-granite.txt"


def is_granite_model(model: str) -> bool:
    return model in (GRANITE_STT_MODEL, GRANITE_STT_MODEL_ID)


def granite_engine() -> str:
    return os.environ.get("ODYSSEUS_GRANITE_ENGINE", "transformers").strip().lower()


def validate_language(language: str) -> None:
    if language.strip().lower() not in ("", *GRANITE_STT_LANGUAGES):
        raise ValueError("Granite STT supports en, fr, de, es, pt, it, ja; leave language empty for automatic recognition")


def dependency_error() -> str | None:
    if not shutil.which("ffmpeg"):
        return "Granite STT requires FFmpeg on PATH"
    engine = granite_engine()
    if engine == "transcribe.cpp":
        from services.stt.transcribe_cpp import dependency_error as native_error
        return native_error()
    if engine != "transformers":
        return "ODYSSEUS_GRANITE_ENGINE must be transformers or transcribe.cpp"
    try:
        if any(importlib.util.find_spec(name) is None for name in ("torch", "torchaudio", "transformers", "numpy")):
            return _INSTALL_HELP
        from packaging.version import Version

        if Version(version("transformers")) < Version("5.16.0"):
            return _INSTALL_HELP
        if any(Version(version(name)) < Version("2.6.0") for name in ("torch", "torchaudio")):
            return _INSTALL_HELP
    except (ImportError, PackageNotFoundError, ValueError):
        return _INSTALL_HELP
    return None


def _decode_audio(source: bytes | Path, start: float = 0.0, end: float | None = None):
    import numpy as np

    if not math.isfinite(start) or start < 0:
        raise ValueError("Audio start must be finite and nonnegative")
    if end is not None and (not math.isfinite(end) or end <= start):
        raise ValueError("Audio end must be finite and after start")
    duration = end - start if end is not None else GRANITE_STT_MAX_AUDIO_SECONDS + 0.1
    if end is not None and duration > GRANITE_STT_MAX_AUDIO_SECONDS:
        raise ValueError(f"Granite STT accepts at most {GRANITE_STT_MAX_AUDIO_SECONDS} seconds per request")
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("Granite STT requires FFmpeg on PATH")
    with tempfile.TemporaryDirectory(prefix="odysseus-stt-") as directory:
        if isinstance(source, bytes):
            path = Path(directory) / "audio"
            path.write_bytes(source)
        else:
            path = source
        result = subprocess.run(
            [
                ffmpeg, "-nostdin", "-v", "error", "-threads", "2",
                "-protocol_whitelist", "file,pipe",
                "-format_whitelist", "wav,mp3,mov,matroska,webm,ogg,flac,aac,avi",
                "-ss", str(start), "-i", str(path), "-t", str(duration),
                "-map", "0:a:0", "-vn", "-ac", "1", "-ar", str(GRANITE_STT_SAMPLE_RATE),
                "-f", "f32le", "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            timeout=60,
            check=False,
        )
    if result.returncode:
        raise ValueError("Granite STT could not decode audio with FFmpeg")
    samples = np.frombuffer(result.stdout, dtype="<f4").copy()
    if samples.size > GRANITE_STT_SAMPLE_RATE * GRANITE_STT_MAX_AUDIO_SECONDS:
        raise ValueError(f"Granite STT accepts at most {GRANITE_STT_MAX_AUDIO_SECONDS} seconds per request; select a shorter range or use Whisper")
    if not np.isfinite(samples).all():
        raise ValueError("Audio contains non-finite samples")
    return samples


class GraniteSTT:
    def __init__(self):
        self._model = None
        self._processor = None
        self._lock = threading.Lock()

    @property
    def loaded(self) -> bool:
        return self._model is not None

    def _load(self):
        if self.loaded:
            return
        import torch
        from transformers import AutoModelForCTC, AutoProcessor

        device = "cuda" if torch.cuda.is_available() else "cpu"
        dtype = torch.bfloat16 if device == "cuda" and torch.cuda.is_bf16_supported() else torch.float32
        options = {"revision": GRANITE_STT_REVISION, "trust_remote_code": False}
        processor = AutoProcessor.from_pretrained(GRANITE_STT_MODEL_ID, **options)
        model = AutoModelForCTC.from_pretrained(
            GRANITE_STT_MODEL_ID, dtype=dtype, use_safetensors=True, **options,
        ).to(device).eval()
        self._processor = processor
        self._model = model

    def transcribe(
        self, source: bytes | Path, language: str = "", *,
        start: float = 0.0, end: float | None = None,
    ) -> str:
        validate_language(language)
        error = dependency_error()
        if error:
            raise RuntimeError(error)
        with self._lock:
            samples = _decode_audio(source, start, end)
            if not samples.size:
                return ""
            self._load()
            import torch

            with torch.inference_mode():
                inputs = self._processor(
                    samples, sampling_rate=GRANITE_STT_SAMPLE_RATE,
                    device=self._model.device, return_tensors="pt",
                )
                inputs = inputs.to(self._model.device, dtype=self._model.dtype)
                tokens = self._model.generate(**inputs)
                return self._processor.batch_decode(tokens, skip_special_tokens=True)[0].strip()


_granite_stt = GraniteSTT()


def get_granite_stt():
    if granite_engine() == "transcribe.cpp":
        from services.stt.transcribe_cpp import get_transcribe_cpp_stt
        return get_transcribe_cpp_stt()
    return _granite_stt
