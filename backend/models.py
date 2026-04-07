"""Pydantic v2 request / response models for VocalTwist API."""
from __future__ import annotations

import re
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_HTML_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<script[\s\S]*?>[\s\S]*?</script>", re.IGNORECASE)
_WHITESPACE_RE = re.compile(r"\s+")

MAX_TTS_LENGTH = 2_000


def _strip_html(value: str) -> str:
    """Remove script blocks then all HTML tags from *value*."""
    value = _SCRIPT_RE.sub("", value)
    value = _HTML_TAG_RE.sub("", value)
    return _WHITESPACE_RE.sub(" ", value).strip()


# ---------------------------------------------------------------------------
# STT models
# ---------------------------------------------------------------------------


class TranscribeResponse(BaseModel):
    """Successful transcription result."""

    text: str = Field(description="Raw transcript returned by the STT engine.")
    display_text: str = Field(
        description="Cleaned transcript suitable for display (title-cased sentences)."
    )
    language: str | None = Field(
        default=None,
        description="Detected or requested ISO language code (e.g. 'en', 'hi').",
    )
    duration_ms: float | None = Field(
        default=None,
        description="Wall-clock milliseconds taken for transcription.",
    )

    model_config = {"json_schema_extra": {"example": {
        "text": "hello how are you",
        "display_text": "Hello how are you",
        "language": "en",
        "duration_ms": 412.3,
    }}}


class AmbientTranscribeResponse(BaseModel):
    """Slim transcription result for ambient / VAD mode."""

    text: str = Field(description="Raw transcript.")
    display_text: str = Field(description="Display-ready transcript.")


# ---------------------------------------------------------------------------
# TTS models
# ---------------------------------------------------------------------------


class SpeakRequest(BaseModel):
    """Request body for the /api/speak endpoint."""

    text: Annotated[str, Field(min_length=1, max_length=MAX_TTS_LENGTH)] = Field(
        description="Text to synthesise. HTML tags are stripped automatically.",
    )
    voice: str | None = Field(
        default=None,
        description="edge-tts voice name (e.g. 'en-US-AriaNeural'). "
        "Falls back to the language-default voice when omitted.",
    )
    language: str | None = Field(
        default=None,
        description="ISO language code used to look up the default voice "
        "when *voice* is not provided (e.g. 'hi').",
    )

    @field_validator("text", mode="before")
    @classmethod
    def sanitize_text(cls, v: str) -> str:
        """Strip HTML / script injection from text before TTS synthesis."""
        if not isinstance(v, str):
            raise ValueError("text must be a string")
        cleaned = _strip_html(v)
        if not cleaned:
            raise ValueError("text must not be empty after stripping HTML")
        return cleaned[:MAX_TTS_LENGTH]

    model_config = {"json_schema_extra": {"example": {
        "text": "Hello, how can I help you today?",
        "voice": "en-US-AriaNeural",
        "language": "en",
    }}}


# ---------------------------------------------------------------------------
# Health / meta models
# ---------------------------------------------------------------------------


class HealthResponse(BaseModel):
    """System health status."""

    status: str = Field(description="'ok' when all subsystems are healthy.")
    version: str = Field(description="VocalTwist package version.")
    stt_provider: str = Field(description="Active STT provider name.")
    tts_provider: str = Field(description="Active TTS provider name.")
    uptime_s: float = Field(description="Seconds since the application started.")

    model_config = {"json_schema_extra": {"example": {
        "status": "ok",
        "version": "0.1.0",
        "stt_provider": "whisper",
        "tts_provider": "edge_tts",
        "uptime_s": 3600.0,
    }}}


class ProvidersResponse(BaseModel):
    """List of available STT and TTS providers."""

    stt: list[str] = Field(description="Registered STT provider names.")
    tts: list[str] = Field(description="Registered TTS provider names.")


class VoiceInfo(BaseModel):
    """Metadata for a single TTS voice."""

    name: str
    language: str
    gender: str | None = None
    description: str | None = None


class VoicesResponse(BaseModel):
    """Available voices grouped by language."""

    voices: dict[str, list[VoiceInfo]] = Field(
        description="Map of language code → list of VoiceInfo."
    )


# ---------------------------------------------------------------------------
# Error model
# ---------------------------------------------------------------------------


class ErrorResponse(BaseModel):
    """Standard error envelope returned on 4xx / 5xx."""

    detail: str
    request_id: str | None = None
