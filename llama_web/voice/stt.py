"""Local speech-to-text with faster-whisper (no cloud, no Lemonade)."""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

class VoiceError(RuntimeError):
    pass


_MODEL = None
_LOCK = threading.Lock()
_AV_PATCHED = False
_CACHE_DIR = Path(os.environ.get(
    "LLAMA_WEB_VOICE_DIR", str(Path.home() / ".local/share/llama-web/voice")))


def _model_name() -> str:
    return os.environ.get("LLAMA_WEB_STT_MODEL", "small")


def _patch_av_compatibility():
    """faster-whisper 1.x passes av.open(metadata_errors=...), which newer PyAV
    releases removed. Drop only that obsolete keyword for our own process."""
    global _AV_PATCHED
    if _AV_PATCHED:
        return
    try:
        import av
    except ImportError:
        return
    original = av.open
    def compatible_open(*args, **kwargs):
        kwargs.pop("metadata_errors", None)
        return original(*args, **kwargs)
    av.open = compatible_open
    _AV_PATCHED = True


def _get_model():
    global _MODEL
    _patch_av_compatibility()
    with _LOCK:
        if _MODEL is None:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise VoiceError(
                    "faster-whisper is not installed; install the voice dependencies "
                    "with requirements.txt.") from exc
            _CACHE_DIR.mkdir(parents=True, exist_ok=True)
            try:
                _MODEL = WhisperModel(_model_name(), device="cpu", compute_type="int8",
                                      download_root=str(_CACHE_DIR / "stt"))
            except Exception as exc:
                raise VoiceError(f"Could not load the local speech model: {exc}") from exc
        return _MODEL


def _to_wav(audio_bytes: bytes) -> bytes:
    if not shutil.which("ffmpeg"):
        raise VoiceError("ffmpeg is required to convert microphone audio (install ffmpeg).")
    with tempfile.NamedTemporaryFile(suffix=".input") as src, \
            tempfile.NamedTemporaryFile(suffix=".wav") as dst:
        src.write(audio_bytes)
        src.flush()
        try:
            subprocess.run(
                ["ffmpeg", "-y", "-i", src.name, "-ar", "16000", "-ac", "1",
                 "-c:a", "pcm_s16le", dst.name],
                capture_output=True, timeout=30, check=True,
            )
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            raise VoiceError(f"Audio conversion failed: {exc}") from exc
        return dst.read()


def transcribe(audio_bytes: bytes, filename: str | None = None,
               language: str | None = None) -> str:
    """Transcribe recorded microphone audio to text, fully on this machine.

    `language` is optional: mixed-language speakers are usually better served by
    Whisper's auto-detection, so only pass it when an administrator configured one.
    """
    if not audio_bytes:
        raise VoiceError("Empty audio recording.")
    wav = _to_wav(audio_bytes)
    with tempfile.NamedTemporaryFile(suffix=".wav") as handle:
        handle.write(wav)
        handle.flush()
        try:
            options = {"beam_size": 5, "vad_filter": True,
                       "condition_on_previous_text": False}
            if language:
                options["language"] = language
            segments, _info = _get_model().transcribe(handle.name, **options)
            text = " ".join(segment.text.strip() for segment in segments if segment.text.strip())
        except Exception as exc:
            raise VoiceError(f"Local transcription failed: {exc}") from exc
    return text.strip()
