"""Optional transcribe.cpp CLI backend for Granite TurboCTC."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import struct
import subprocess
import tempfile
import threading

from src.constants import GRANITE_STT_MODEL_ID, GRANITE_STT_REVISION, GRANITE_STT_SAMPLE_RATE

TRANSCRIBE_CPP_REVISION = "5700f127e38dd53c4735ea23ef743b4b538eacd4"
_MAX_TEXT_BYTES = 2 * 1024 * 1024
_MANIFEST_SUFFIX = ".provenance.json"
_BACKENDS = frozenset({"auto", "cpu", "cpu_accel", "cuda", "rocm", "vulkan", "metal"})
_SAFE_ENV = frozenset({
    "PATH", "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH", "LANG", "LC_ALL",
    "CUDA_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES", "VK_ICD_FILENAMES",
    "OMP_NUM_THREADS", "TRANSCRIBE_NO_MMAP", "TRANSCRIBE_NATIVE_PROVIDER",
})


def _configuration() -> tuple[str, Path, str, int, str]:
    binary = shutil.which(os.environ.get("ODYSSEUS_TRANSCRIBE_CPP_BIN") or "transcribe-cli")
    if binary is None:
        raise ValueError("Install transcribe.cpp and set ODYSSEUS_TRANSCRIBE_CPP_BIN to transcribe-cli")

    model_value = os.environ.get("ODYSSEUS_GRANITE_GGUF", "")
    model_path = Path(model_value).expanduser()
    if not model_value or not model_path.is_absolute() or model_path.suffix.lower() != ".gguf":
        raise ValueError("Set ODYSSEUS_GRANITE_GGUF to an absolute prepared .gguf file")
    model_path = model_path.resolve(strict=True)
    if not model_path.is_file():
        raise ValueError("Configured Granite GGUF is not a file")

    manifest_path = Path(str(model_path) + _MANIFEST_SUFFIX)
    try:
        with manifest_path.open("rb") as handle:
            raw = handle.read(16_385)
        if len(raw) > 16_384:
            raise ValueError("Granite model provenance is too large")
        provenance = json.loads(raw)
    except (OSError, ValueError, UnicodeError) as exc:
        raise ValueError("Prepare the pinned multilingual GGUF with scripts/prepare_granite_cpp.py") from exc

    if not isinstance(provenance, dict) or (
        provenance.get("model_id") != GRANITE_STT_MODEL_ID
        or provenance.get("revision") != GRANITE_STT_REVISION
        or provenance.get("engine_revision") != TRANSCRIBE_CPP_REVISION
        or provenance.get("tokenizer") != "bpe"
        or not re.fullmatch(r"[0-9a-f]{64}", str(provenance.get("sha256", "")))
    ):
        raise ValueError("Granite GGUF provenance is not the pinned multilingual checkpoint")

    backend = os.environ.get("ODYSSEUS_TRANSCRIBE_CPP_BACKEND", "auto")
    if backend not in _BACKENDS:
        raise ValueError("ODYSSEUS_TRANSCRIBE_CPP_BACKEND is invalid")
    try:
        threads = int(os.environ.get("ODYSSEUS_TRANSCRIBE_CPP_THREADS", "2"))
    except ValueError as exc:
        raise ValueError("ODYSSEUS_TRANSCRIBE_CPP_THREADS must be 1..64") from exc
    if not 1 <= threads <= 64:
        raise ValueError("ODYSSEUS_TRANSCRIBE_CPP_THREADS must be 1..64")
    return str(Path(binary).resolve()), model_path, backend, threads, provenance["sha256"]


def dependency_error() -> str | None:
    try:
        _configuration()
    except (OSError, ValueError) as exc:
        return f"transcribe.cpp unavailable: {exc}"
    return None


def _identity(path: Path) -> tuple:
    stat = path.stat()
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


class TranscribeCppSTT:
    def __init__(self):
        self._lock = threading.Lock()
        self._verified: tuple | None = None

    @property
    def loaded(self) -> bool:
        return False

    def _check_model(self, model_path: Path, expected_sha256: str) -> None:
        identity = _identity(model_path)
        cache_key = (str(model_path), identity, expected_sha256)
        if cache_key == self._verified:
            return
        with model_path.open("rb") as source:
            if source.read(4) != b"GGUF":
                raise ValueError("Granite model is not GGUF")
            source.seek(0)
            actual_sha256 = hashlib.file_digest(source, "sha256").hexdigest()
        if actual_sha256 != expected_sha256 or _identity(model_path) != identity:
            raise ValueError("Granite GGUF checksum mismatch; prepare the model again")
        self._verified = cache_key

    def transcribe(
        self, source: bytes | Path, language: str = "", *,
        start: float = 0.0, end: float | None = None,
    ) -> str:
        from services.stt.granite import _decode_audio, validate_language

        validate_language(language)
        with self._lock:
            binary, model, backend, threads, digest = _configuration()
            self._check_model(model, digest)
            samples = _decode_audio(source, start, end)
            if not samples.size:
                return ""
            pcm = samples.astype("<f4", copy=False).tobytes()
            with tempfile.TemporaryDirectory(prefix="odysseus-transcribe-") as directory:
                audio = Path(directory) / "input.wav"
                output = Path(directory) / "output.txt"
                audio.write_bytes(struct.pack(
                    "<4sI4s4sIHHIIHH4sI",
                    b"RIFF", 36 + len(pcm), b"WAVE", b"fmt ",
                    16, 3, 1, GRANITE_STT_SAMPLE_RATE,
                    GRANITE_STT_SAMPLE_RATE * 4, 4, 32, b"data", len(pcm),
                ) + pcm)
                environment = {name: value for name, value in os.environ.items() if name in _SAFE_ENV}
                environment["HOME"] = directory
                environment["TMPDIR"] = directory
                try:
                    result = subprocess.run(
                        [binary, "--model", str(model), "--backend", backend,
                         "--threads", str(threads), "--timestamps", "none",
                         "--quiet", "--output", str(output), str(audio)],
                        cwd=directory, env=environment, stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        timeout=300, check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    raise RuntimeError("transcribe.cpp inference timed out") from exc
                if result.returncode:
                    raise RuntimeError(f"transcribe.cpp failed with exit code {result.returncode}")
                try:
                    with output.open("rb") as handle:
                        result_bytes = handle.read(_MAX_TEXT_BYTES + 1)
                except FileNotFoundError as exc:
                    raise RuntimeError("transcribe.cpp returned no transcript file") from exc
                if len(result_bytes) > _MAX_TEXT_BYTES:
                    raise ValueError("transcribe.cpp output exceeds the transcript limit")
                return result_bytes.decode("utf-8").strip()


_native_stt = TranscribeCppSTT()


def get_transcribe_cpp_stt() -> TranscribeCppSTT:
    return _native_stt
