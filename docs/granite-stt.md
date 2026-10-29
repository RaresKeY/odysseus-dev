# Granite Speech 5.0 TurboCTC

Odysseus supports the optional multilingual `ibm-granite/granite-speech-5.0-470m-turboctc` model for microphone STT and `transcribe_media`. Whisper remains the default. No weights or heavyweight dependencies are installed by default.

## Engine and installation

Existing local transcription uses **faster-whisper / CTranslate2**. Granite is not a Whisper checkpoint; this adapter uses **Transformers >=5.16 / PyTorch / TorchAudio**. Install in the application's virtual environment:

```sh
python -m pip install -r requirements-stt-granite.txt
```

FFmpeg must be on `PATH`. TorchAudio is required by Granite's feature extractor even though FFmpeg handles audio decoding. Install mutually compatible CPU or CUDA builds of PyTorch and TorchAudio; GPU passthrough alone does not install them. Docker users must add the optional requirements and FFmpeg to their image; the default image is unchanged.

The processor and safetensors weights are pinned to IBM revision `1dd23f46f6d4e6eae2531982ba2e33220086dd97`, the October 21, 2026 multilingual update, with `trust_remote_code=False`. They download into the standard Hugging Face cache on first transcription, not on status checks. Preserve that cache for offline reuse (`HF_HOME` / `HF_HUB_OFFLINE`).

CPU uses float32; CUDA uses bfloat16 when supported, otherwise float32. The 470M parameters alone need approximately 1.9 GB in float32 or 0.94 GB in bfloat16, plus runtime and audio-dependent activation memory. These are weight-size estimates, not measured total RAM/VRAM requirements. Metal/MPS and quantization are not enabled by this adapter.

## Microphone configuration

Update the existing admin-only `POST /api/auth/settings` endpoint:

```json
{
  "stt_enabled": true,
  "stt_provider": "local",
  "stt_model": "granite-speech-5.0-470m-turboctc",
  "stt_language": ""
}
```

The full `ibm-granite/...` ID is also accepted. Existing `/api/stt/transcribe` uploads then use Granite. `/api/stt/stats` reports prerequisites, revision, engine and load state without loading weights. Availability means installed prerequisites, not a proven successful model load. Route work runs outside the asynchronous event loop.

The frontend currently has no visible STT settings panel; configure through the existing settings API. To restore Whisper, set `stt_model` to `base` with the same local provider.

## Agent transcription

Call `transcribe_media` with:

```json
{
  "path": "/workspace/interview.wav",
  "model": "granite-speech-5.0-470m-turboctc",
  "start": "0",
  "end": "120",
  "output_path": "/workspace/interview.txt"
}
```

The existing `ODYSSEUS_STT_MODEL` environment variable can also select the tool's default, separately from microphone settings. Workspace confinement is unchanged. Inline output is bounded; the complete transcript is saved to disk.

Supported languages are **en, fr, de, es, pt, it, ja**; Romanian is not supported. A language hint validates intended use but does not force decoding. Granite provides plain text, without detected-language metadata or timestamps. `.srt`, `.vtt`, `.jsonl` and `force_language=true` requests fail explicitly; use Whisper for those features.

Each call accepts at most **300 seconds** of decoded audio. Longer input is rejected, not silently truncated; select a shorter start/end range or use Whisper. This is an integration resource limit, not the model's maximum duration. FFmpeg decoding has a 60-second timeout, converts to mono 16 kHz float32, and excludes playlist demuxers and network protocols. Model loading/inference is serialized per process. Streaming and diarization are not implemented.

## transcribe.cpp and licenses

[transcribe.cpp](https://github.com/handy-computer/transcribe.cpp) is an MIT-licensed native alternative with CPU/Metal/Vulkan/CUDA/ROCm backends. However, its [published Handy GGUF](https://huggingface.co/handy-computer/granite-speech-5.0-470m-turboctc-gguf), checked October 29, 2026, still identifies the older **English-only** IBM revision `18ca3c1`. Supporting the current multilingual release through that engine needs conversion, vocabulary/parity validation and platform testing; this PR does not claim the engine cannot support it.

The [IBM model](https://huggingface.co/ibm-granite/granite-speech-5.0-470m-turboctc) and [Transformers](https://github.com/huggingface/transformers/blob/main/LICENSE) are **Apache-2.0**. [PyTorch](https://github.com/pytorch/pytorch/blob/main/LICENSE) and [TorchAudio](https://github.com/pytorch/audio/blob/main/LICENSE) use BSD-style licenses with third-party notices. Retain applicable licenses and NOTICE files when redistributing dependencies or weights. Odysseus remains **AGPL-3.0**.

The separate [`-nc` checkpoint](https://huggingface.co/ibm-granite/granite-speech-5.0-470m-turboctc-nc) is **CC-BY-NC-SA-4.0** and is not registered by this adapter. It is not a commercial-use substitute for the Apache-licensed checkpoint.

Reference: [Transformers Granite Speech 5 API](https://huggingface.co/docs/transformers/model_doc/granite_speech5).
