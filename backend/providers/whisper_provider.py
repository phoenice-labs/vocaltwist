"""WhisperSTTProvider — faster-whisper backed Speech-to-Text provider.

Ported from the original ``whisper_service.py``.  Key design decisions:

* **Lazy model loading** — the ``WhisperModel`` is created the first time
  ``transcribe()`` is called, not at import time.  This keeps startup fast
  and avoids GPU/CPU memory allocation for requests that never hit the STT
  endpoint.
* **Thread-pool offload** — ``WhisperModel.transcribe()`` is CPU-bound and
  synchronous.  We use ``asyncio.to_thread()`` so it never blocks the
  FastAPI event loop.
* **Temp-file pattern** — faster-whisper requires a file path, not a bytes
  buffer, so we write to a NamedTemporaryFile and always clean it up in a
  ``finally`` block.
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile

from .base import STTProvider

logger = logging.getLogger(__name__)

# Module-level singleton — shared across all requests within a process.
_model = None
_model_lock = asyncio.Lock()


def _load_model(model_size: str, device: str, compute_type: str):
    """Synchronously load and return a WhisperModel instance."""
    from faster_whisper import WhisperModel  # noqa: PLC0415 – lazy import

    logger.info(
        "Loading faster-whisper model",
        extra={"model": model_size, "device": device, "compute_type": compute_type},
    )
    return WhisperModel(model_size, device=device, compute_type=compute_type)


def _transcribe_sync(
    tmp_path: str,
    task: str,
    language: str | None,
    vad_filter: bool,
    model_size: str,
    device: str,
    compute_type: str,
) -> str:
    """Synchronous transcription — intended to run inside a thread pool."""
    global _model
    if _model is None:
        _model = _load_model(model_size, device, compute_type)

    kwargs: dict = {"task": task, "vad_filter": vad_filter}
    if language:
        kwargs["language"] = language

    segments, _ = _model.transcribe(tmp_path, **kwargs)
    return " ".join(seg.text for seg in segments).strip()


class WhisperSTTProvider(STTProvider):
    """STT provider backed by `faster-whisper`."""

    name: str = "whisper"

    def __init__(
        self,
        model_size: str = "base",
        device: str = "cpu",
        compute_type: str = "int8",
    ) -> None:
        self._model_size = model_size
        self._device = device
        self._compute_type = compute_type

    @classmethod
    def is_available(cls) -> bool:
        """Return ``True`` if ``faster-whisper`` is installed."""
        try:
            import faster_whisper  # noqa: F401
            return True
        except ImportError:
            return False

    async def transcribe(
        self,
        audio_bytes: bytes,
        content_type: str = "audio/webm",
        task: str = "transcribe",
        language: str | None = None,
        vad_filter: bool = True,
    ) -> str:
        """Transcribe *audio_bytes* using faster-whisper.

        The synchronous model call is offloaded to a thread pool via
        ``asyncio.to_thread`` so the event loop is never blocked.

        Args:
            audio_bytes:  Raw audio data.
            content_type: MIME type — used to choose the temp-file suffix.
            task:         ``"transcribe"`` or ``"translate"``.
            language:     ISO 639-1 language hint; ``None`` = auto-detect.
            vad_filter:   Apply Silero VAD pre-filter to remove silence.

        Returns:
            Plain-text transcript (empty string for silent / inaudible audio).

        Raises:
            RuntimeError: If faster-whisper is not installed or transcription
                          fails unexpectedly.
        """
        if not self.is_available():
            raise RuntimeError(
                "faster-whisper is not installed. "
                "Run: pip install faster-whisper"
            )

        suffix = _content_type_to_suffix(content_type)
        tmp_path: str | None = None

        try:
            # Write bytes to a temp file — faster-whisper needs a file path.
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
                tmp.write(audio_bytes)
                tmp_path = tmp.name

            logger.debug(
                "Starting transcription",
                extra={
                    "task": task,
                    "language": language,
                    "vad_filter": vad_filter,
                    "bytes": len(audio_bytes),
                },
            )

            text = await asyncio.to_thread(
                _transcribe_sync,
                tmp_path,
                task,
                language,
                vad_filter,
                self._model_size,
                self._device,
                self._compute_type,
            )

            logger.debug("Transcription complete", extra={"chars": len(text)})
            return text

        except Exception as exc:
            logger.exception("Transcription failed", extra={"error": str(exc)})
            raise RuntimeError(f"Whisper transcription failed: {exc}") from exc
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except OSError:
                    logger.warning("Could not delete temp file", extra={"path": tmp_path})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MIME_TO_SUFFIX: dict[str, str] = {
    "audio/webm": ".webm",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mp4": ".mp4",
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/flac": ".flac",
}


def _content_type_to_suffix(content_type: str) -> str:
    """Map a MIME type to an appropriate file extension for ffmpeg."""
    # Strip quality params (e.g. "audio/webm; codecs=opus")
    base = content_type.split(";")[0].strip().lower()
    return _MIME_TO_SUFFIX.get(base, ".webm")
