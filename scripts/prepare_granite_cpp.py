#!/usr/bin/env python3
"""Convert the pinned multilingual Granite checkpoint for transcribe.cpp."""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services.stt.transcribe_cpp import TRANSCRIBE_CPP_REVISION
from src.constants import GRANITE_STT_LANGUAGES, GRANITE_STT_MODEL_ID, GRANITE_STT_REVISION

CONVERTER_BLOB = "f3578f672bf8a8f5015b5de3ab3a6f910a4c3ed7"
_ORIGINAL_LANGUAGES = 'languages=["en"],'
_ORIGINAL_DESCRIPTION = '"English encoder-CTC ASR. 16-block Granite Conformer encoder with "'
_ORIGINAL_PREPROCESSOR = '    preproc = json.loads((model_dir / "preprocessor_config.json").read_text())'
_MANIFEST_SUFFIX = ".provenance.json"


def _git(source: Path, *args: str) -> str:
    return subprocess.check_output(
        ["git", "-C", str(source), *args], text=True, timeout=20,
    ).strip()


def prepare(checkout: Path, outdir: Path) -> Path:
    checkout = checkout.resolve(strict=True)
    outdir = outdir.resolve()
    if _git(checkout, "rev-parse", "HEAD") != TRANSCRIBE_CPP_REVISION:
        raise ValueError("transcribe.cpp source must be checked out at the pinned revision")
    converter = checkout / "scripts" / "convert-granite5_ctc.py"
    if _git(checkout, "hash-object", str(converter)) != CONVERTER_BLOB:
        raise ValueError("transcribe.cpp converter differs from the reviewed revision")
    if _git(checkout, "status", "--porcelain", "--untracked-files=no"):
        raise ValueError("The pinned transcribe.cpp checkout contains modified tracked files")
    if outdir.exists() and any(outdir.iterdir()):
        raise ValueError("Choose a new or empty output directory")
    outdir.mkdir(parents=True, exist_ok=True)

    source = converter.read_text(encoding="utf-8")
    if any(source.count(marker) != 1 for marker in (
        _ORIGINAL_LANGUAGES, _ORIGINAL_DESCRIPTION, _ORIGINAL_PREPROCESSOR,
    )):
        raise ValueError("The reviewed converter's multilingual metadata markers changed")
    language_list = repr(list(GRANITE_STT_LANGUAGES))
    source = source.replace(_ORIGINAL_LANGUAGES, f"languages={language_list},")
    source = source.replace(_ORIGINAL_DESCRIPTION, '"Multilingual encoder-CTC ASR. 16-block Granite Conformer encoder with "')
    source = source.replace(
        _ORIGINAL_PREPROCESSOR,
        '    preprocessor_path = model_dir / "preprocessor_config.json"\n'
        '    preproc = json.loads(preprocessor_path.read_text()) if preprocessor_path.is_file() else {}',
    )

    ast.parse(source)
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="odysseus_granite_", dir=converter.parent,
        encoding="utf-8", delete=False,
    ) as overlay:
        overlay.write(source)
        overlay_path = Path(overlay.name)
    try:
        subprocess.run(
            [sys.executable, str(overlay_path), GRANITE_STT_MODEL_ID,
             "--revision", GRANITE_STT_REVISION, "--outdir", str(outdir)],
            cwd=checkout, timeout=3600, check=True,
        )
    finally:
        overlay_path.unlink(missing_ok=True)

    models = list(outdir.glob("*.gguf"))
    if len(models) != 1 or models[0].is_symlink() or not models[0].is_file():
        raise RuntimeError("Expected exactly one regular newly converted GGUF")

    from huggingface_hub import hf_hub_download

    tokenizer_path = hf_hub_download(
        repo_id=GRANITE_STT_MODEL_ID, filename="tokenizer.json", revision=GRANITE_STT_REVISION,
    )
    decoder = json.loads(Path(tokenizer_path).read_text(encoding="utf-8"))["decoder"]
    if decoder.get("type") != "Sequence" or not any(
        item.get("type") == "Replace" and item.get("pattern", {}).get("String") == "▁"
        for item in decoder.get("decoders", [])
    ):
        raise ValueError("Pinned multilingual tokenizer does not match the required BPE variant")

    model_path = models[0].resolve()
    with model_path.open("rb") as handle:
        if handle.read(4) != b"GGUF":
            raise ValueError("Converter did not produce a GGUF file")
        handle.seek(0)
        checksum = hashlib.file_digest(handle, "sha256").hexdigest()

    manifest = {
        "model_id": GRANITE_STT_MODEL_ID,
        "revision": GRANITE_STT_REVISION,
        "engine_revision": TRANSCRIBE_CPP_REVISION,
        "tokenizer": "bpe",
        "sha256": checksum,
    }
    Path(str(model_path) + _MANIFEST_SUFFIX).write_text(
        json.dumps(manifest, indent=2) + "\n", encoding="utf-8",
    )
    return model_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transcribe-source", type=Path, required=True)
    parser.add_argument("--outdir", type=Path, required=True)
    args = parser.parse_args()
    print(prepare(args.transcribe_source, args.outdir))


if __name__ == "__main__":
    main()
