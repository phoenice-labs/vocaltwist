"""EdgeTTSProvider — Microsoft Edge neural TTS provider.

Ported from the original ``tts_service.py``.  Uses the ``edge-tts`` package
which streams audio from Microsoft's neural TTS service — free, high quality,
and offline-capable after the first use (responses are not cached by default).

Available voices used in VocalTwist defaults
--------------------------------------------
* en-US-AriaNeural    — English (US)
* hi-IN-SwaraNeural   — Hindi
* mr-IN-AarohiNeural  — Marathi
* es-ES-ElviraNeural  — Spanish
* fr-FR-DeniseNeural  — French
* pt-BR-FranciscaNeural — Portuguese (Brazil)
* de-DE-KatjaNeural   — German
* zh-CN-XiaoxiaoNeural — Chinese (Mandarin)
* ja-JP-NanamiNeural  — Japanese
* ar-SA-ZariyahNeural — Arabic

Run ``edge-tts --list-voices`` to see the complete list of ~400+ voices.
"""
from __future__ import annotations

import io
import logging

from .base import TTSProvider

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "en-US-AriaNeural"

# Language → default voice mapping (mirrors VocalTwistSettings.voice_for_lang)
_LANG_VOICE_MAP: dict[str, str] = {
    "en": "en-US-AriaNeural",
    "hi": "hi-IN-SwaraNeural",
    "mr": "mr-IN-AarohiNeural",
    "es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
    "pt": "pt-BR-FranciscaNeural",
    "de": "de-DE-KatjaNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "ja": "ja-JP-NanamiNeural",
    "ar": "ar-SA-ZariyahNeural",
}

# All voices shipped with this provider (used by /api/voices)
AVAILABLE_VOICES: list[dict] = [
    {"name": "en-US-AriaNeural",        "language": "en", "gender": "Female"},
    {"name": "en-US-GuyNeural",         "language": "en", "gender": "Male"},
    {"name": "en-GB-SoniaNeural",       "language": "en", "gender": "Female"},
    {"name": "hi-IN-SwaraNeural",       "language": "hi", "gender": "Female"},
    {"name": "hi-IN-MadhurNeural",      "language": "hi", "gender": "Male"},
    {"name": "mr-IN-AarohiNeural",      "language": "mr", "gender": "Female"},
    {"name": "mr-IN-ManoharNeural",     "language": "mr", "gender": "Male"},
    {"name": "es-ES-ElviraNeural",      "language": "es", "gender": "Female"},
    {"name": "fr-FR-DeniseNeural",      "language": "fr", "gender": "Female"},
    {"name": "pt-BR-FranciscaNeural",   "language": "pt", "gender": "Female"},
    {"name": "de-DE-KatjaNeural",       "language": "de", "gender": "Female"},
    {"name": "zh-CN-XiaoxiaoNeural",    "language": "zh", "gender": "Female"},
    {"name": "ja-JP-NanamiNeural",      "language": "ja", "gender": "Female"},
    {"name": "ar-SA-ZariyahNeural",     "language": "ar", "gender": "Female"},
]


class EdgeTTSProvider(TTSProvider):
    """TTS provider backed by ``edge-tts`` (Microsoft Edge neural TTS)."""

    name: str = "edge_tts"

    def __init__(self, default_voice: str = DEFAULT_VOICE) -> None:
        self._default_voice = default_voice

    @classmethod
    def is_available(cls) -> bool:
        """Return ``True`` if ``edge-tts`` is installed."""
        try:
            import edge_tts  # noqa: F401
            return True
        except ImportError:
            return False

    async def speak(
        self,
        text: str,
        voice: str | None = None,
        language: str | None = None,
    ) -> bytes:
        """Synthesise *text* and return MP3 audio bytes.

        Voice resolution order:
        1. Explicit *voice* parameter.
        2. Language-default voice looked up from *language* code.
        3. Provider-level default (``en-US-AriaNeural``).

        Args:
            text:     Pre-sanitised text to synthesise.
            voice:    edge-tts voice name override.
            language: ISO 639-1 code used for automatic voice selection.

        Returns:
            MP3-encoded audio bytes.

        Raises:
            RuntimeError: If ``edge-tts`` is not installed or synthesis fails.
        """
        if not self.is_available():
            raise RuntimeError(
                "edge-tts is not installed. Run: pip install edge-tts"
            )

        import edge_tts  # noqa: PLC0415 – lazy import

        selected_voice = voice or _LANG_VOICE_MAP.get(
            (language or "").split("-")[0].lower(), self._default_voice
        )

        logger.debug(
            "Starting TTS synthesis",
            extra={"voice": selected_voice, "chars": len(text)},
        )

        try:
            communicate = edge_tts.Communicate(text, selected_voice)
            audio_buffer = io.BytesIO()

            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_buffer.write(chunk["data"])

            audio_bytes = audio_buffer.getvalue()

            if not audio_bytes:
                raise RuntimeError(
                    f"edge-tts returned no audio for voice '{selected_voice}'. "
                    "The voice name may be invalid or the service is unavailable."
                )

            logger.debug(
                "TTS synthesis complete",
                extra={"voice": selected_voice, "audio_bytes": len(audio_bytes)},
            )
            return audio_bytes

        except RuntimeError:
            raise
        except Exception as exc:
            logger.exception("TTS synthesis failed", extra={"error": str(exc)})
            raise RuntimeError(f"edge-tts synthesis failed: {exc}") from exc

    @staticmethod
    def list_voices() -> list[dict]:
        """Return the static list of voices bundled with this provider."""
        return AVAILABLE_VOICES
