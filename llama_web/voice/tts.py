"""Local text-to-speech with Piper (no cloud, no Lemonade)."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from huggingface_hub import hf_hub_download

from llama_web.voice.stt import VoiceError, _CACHE_DIR

_LOCK = threading.Lock()
_VOICES: dict[str, Path] = {}
_CONFIGS: dict[str, Path] = {}
_REPO = "rhasspy/piper-voices"
DEFAULT_VOICE = os.environ.get("LLAMA_WEB_TTS_VOICE", "en_US-lessac-medium")
_VOICE_PATHS = {
    "en_US-lessac-medium": "en/en_US/lessac/medium/en_US-lessac-medium.onnx",
    "en_US-lessac-low": "en/en_US/lessac/low/en_US-lessac-low.onnx",
    "en_GB-alan-medium": "en/en_GB/alan/medium/en_GB-alan-medium.onnx",
}


def available_voices() -> list[str]:
    return list(_VOICE_PATHS)


def _model_path(voice: str) -> Path:
    with _LOCK:
        if voice in _VOICES:
            return _VOICES[voice]
        relative = _VOICE_PATHS.get(voice)
        if not relative:
            raise VoiceError(f"Unknown local voice: {voice}")
        target = _CACHE_DIR / "tts"
        target.mkdir(parents=True, exist_ok=True)
        try:
            model = Path(hf_hub_download(_REPO, relative, cache_dir=str(target)))
        except Exception as exc:
            raise VoiceError(f"Could not download the local voice model: {exc}") from exc
        _VOICES[voice] = model
        config = Path(hf_hub_download(_REPO, relative + ".json", cache_dir=str(target)))
        _CONFIGS[voice] = config
        return model


def synthesize(text: str, voice: str | None = None) -> bytes:
    """Synthesize text to WAV bytes with the local Piper voice."""
    text = (text or "").strip()
    if not text:
        raise VoiceError("No text to speak.")
    voice = voice or DEFAULT_VOICE
    bundled = Path(sys.executable).parent / "piper"
    executable = str(bundled) if bundled.is_file() else shutil.which("piper")
    if not executable:
        raise VoiceError("The piper executable is not installed; install piper-tts.")
    model = _model_path(voice)
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as out:
        output = Path(out.name)
    try:
        result = subprocess.run(
            [executable, "--model", str(model), "--config", str(_CONFIGS[voice]),
             "--output_file", str(output)],
            input=text.encode(), capture_output=True, timeout=120,
        )
        if result.returncode != 0:
            raise VoiceError(result.stderr.decode(errors="replace")[:300] or "Piper failed.")
        return output.read_bytes()
    finally:
        output.unlink(missing_ok=True)
