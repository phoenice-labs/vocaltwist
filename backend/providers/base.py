"""Abstract base classes for VocalTwist STT and TTS providers."""
from __future__ import annotations

from abc import ABC, abstractmethod


class STTProvider(ABC):
    """Abstract Speech-to-Text provider interface."""

    #: Unique identifier for this provider (e.g. ``"whisper"``).
    name: str = "base_stt"

    @classmethod
    def is_available(cls) -> bool:
        """Return ``True`` if the provider's dependencies are installed and
        the provider can be instantiated without errors.

        Sub-classes should override this to perform an import-level check so
        that the registry can degrade gracefully when optional packages are
        absent.
        """
        return True

    @abstractmethod
    async def transcribe(
        self,
        audio_bytes: bytes,
        content_type: str = "audio/webm",
        task: str = "transcribe",
        language: str | None = None,
        vad_filter: bool = True,
    ) -> str:
        """Transcribe *audio_bytes* and return the plain-text transcript.

        Args:
            audio_bytes:  Raw audio data as received from the client.
            content_type: MIME type of the audio (used to choose a temp-file
                          suffix so the decoder picks the right demuxer).
            task:         ``"transcribe"`` — return text in the source language;
                          ``"translate"``  — translate to English.
            language:     ISO 639-1 code hint (e.g. ``"hi"``).  ``None`` means
                          auto-detect.
            vad_filter:   When ``True`` apply a Voice Activity Detection filter
                          to strip silence before feeding audio to the model.

        Returns:
            Plain-text transcript string (may be empty for silent input).
        """


class TTSProvider(ABC):
    """Abstract Text-to-Speech provider interface."""

    #: Unique identifier for this provider (e.g. ``"edge_tts"``).
    name: str = "base_tts"

    @classmethod
    def is_available(cls) -> bool:
        """Return ``True`` if the provider's dependencies are installed."""
        return True

    @abstractmethod
    async def speak(
        self,
        text: str,
        voice: str | None = None,
        language: str | None = None,
    ) -> bytes:
        """Synthesise *text* and return raw MP3 audio bytes.

        Args:
            text:     Pre-sanitised text to synthesise (HTML already stripped).
            voice:    Provider-specific voice identifier.  ``None`` means use
                      the provider's built-in default.
            language: ISO 639-1 language code used to pick a default voice
                      when *voice* is ``None``.

        Returns:
            MP3-encoded audio bytes.
        """
