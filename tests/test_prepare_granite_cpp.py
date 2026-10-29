"""Pinned native GGUF preparation rules."""

import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from scripts import prepare_granite_cpp as prepare
from services.stt.transcribe_cpp import TRANSCRIBE_CPP_REVISION
from src.constants import GRANITE_STT_REVISION


@pytest.fixture
def checkout(tmp_path, monkeypatch):
    root = tmp_path / "transcribe.cpp"
    scripts = root / "scripts"
    scripts.mkdir(parents=True)
    converter = scripts / "convert-granite5_ctc.py"
    converter.write_text(
        'languages=["en"],\n'
        '"English encoder-CTC ASR. 16-block Granite Conformer encoder with "\n',
    )
    monkeypatch.setattr(prepare, "_git", lambda root, *args: (
        TRANSCRIBE_CPP_REVISION if args == ("rev-parse", "HEAD")
        else "" if args == ("status", "--porcelain", "--untracked-files=no")
        else prepare.CONVERTER_BLOB
    ))
    return root


def test_preparation_pins_revision_and_changes_multilingual_metadata(checkout, tmp_path, monkeypatch):
    converted = {}

    def convert(args, **kwargs):
        assert "--revision" in args
        assert args[args.index("--revision") + 1] == GRANITE_STT_REVISION
        overlay = Path(args[1])
        contents = overlay.read_text()
        assert "languages=['en', 'fr', 'de', 'es', 'pt', 'it', 'ja']" in contents
        assert "Multilingual encoder-CTC ASR" in contents
        assert 'languages=["en"]' not in contents
        converted["overlay"] = overlay
        out = Path(args[args.index("--outdir") + 1])
        (out / "model-BF16.gguf").write_bytes(b"GGUFmodel")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(prepare.subprocess, "run", convert)
    tokenizer = tmp_path / "tokenizer.json"
    tokenizer.write_text(json.dumps({
        "decoder": {"type": "Sequence", "decoders": [
            {"type": "Replace", "pattern": {"String": "▁"}}
        ]}
    }))
    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(
        hf_hub_download=lambda **kwargs: str(tokenizer),
    ))
    path = prepare.prepare(checkout, tmp_path / "out")
    assert not converted["overlay"].exists()
    assert path.read_bytes() == b"GGUFmodel"
    manifest = json.loads(Path(str(path) + ".provenance.json").read_text())
    assert manifest["sha256"] == hashlib.sha256(b"GGUFmodel").hexdigest()
    assert manifest["revision"] == GRANITE_STT_REVISION
    assert manifest["engine_revision"] == TRANSCRIBE_CPP_REVISION
    assert manifest["tokenizer"] == "bpe"


def test_preparation_refuses_existing_output(checkout, tmp_path):
    output = tmp_path / "out"
    output.mkdir()
    (output / "prior.gguf").write_bytes(b"data")
    with pytest.raises(ValueError, match="new or empty"):
        prepare.prepare(checkout, output)


@pytest.mark.parametrize("what", ["head", "converter", "markers"])
def test_preparation_rejects_unreviewed_source(checkout, tmp_path, monkeypatch, what):
    if what == "head":
        monkeypatch.setattr(prepare, "_git", lambda root, *args: "a" * 40)
    elif what == "converter":
        monkeypatch.setattr(
            prepare, "_git",
            lambda root, *args: TRANSCRIBE_CPP_REVISION if args == ("rev-parse", "HEAD") else "b" * 40,
        )
    else:
        (checkout / "scripts" / "convert-granite5_ctc.py").write_text("stale")
    with pytest.raises(ValueError):
        prepare.prepare(checkout, tmp_path / "out")
