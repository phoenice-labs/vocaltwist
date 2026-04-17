"""VocalTwist configuration — loaded from environment / .env file."""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any, Type

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict, DotEnvSettingsSource


class _CommaSafeDotEnvSource(DotEnvSettingsSource):
    """DotEnv source that falls back to the raw string when JSON decoding fails.

    pydantic-settings 2.5.x (before env_list_delimiter was added in 2.6) always
    calls ``json.loads()`` on list-typed fields.  That breaks comma-separated
    values like ``VOCALTWIST_CORS_ORIGINS=*``.  Returning the raw string here
    lets the ``field_validator(mode="before")`` handlers do the splitting.
    """

    def decode_complex_value(  # type: ignore[override]
        self, field_name: str, field_info: Any, value: Any
    ) -> Any:
        try:
            return super().decode_complex_value(field_name, field_info, value)
        except Exception:
            return value  # hand the raw string to the field_validator


class VocalTwistSettings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_prefix="VOCALTWIST_",
        extra="ignore",
        case_sensitive=False,
    )

    # ── STT ────────────────────────────────────────────────────────────────────
    stt_provider: str = Field("whisper", description="STT provider: 'whisper' | plugin name")
    whisper_model: str = Field("base", description="Whisper model size: tiny|base|small|medium|large")
    whisper_device: str = Field("cpu", description="Compute device: cpu|cuda")
    whisper_compute_type: str = Field("int8", description="Quantization: int8|float16|float32")
    whisper_vad_filter: bool = Field(True, description="Enable Silero VAD pre-filter")

    # ── TTS ────────────────────────────────────────────────────────────────────
    tts_provider: str = Field("edge_tts", description="TTS provider: 'edge_tts' | plugin name")
    default_voice: str = Field("en-US-AriaNeural", description="Default edge-tts voice")
    default_language: str = Field("en", description="Default language ISO code")

    # ── Security ───────────────────────────────────────────────────────────────
    api_key_enabled: bool = Field(False, description="Require X-API-Key header")
    api_keys: list[str] = Field(default_factory=list, description="Comma-separated valid API keys")
    rate_limit_enabled: bool = Field(True, description="Enable per-IP rate limiting")
    rate_limit_transcribe: str = Field("20/minute", description="Rate limit for /api/transcribe")
    rate_limit_speak: str = Field("30/minute", description="Rate limit for /api/speak")

    # ── Audio Limits ───────────────────────────────────────────────────────────
    max_audio_bytes: int = Field(10 * 1024 * 1024, description="Max audio upload size (bytes)")
    allowed_audio_types: list[str] = Field(
        default_factory=lambda: [
            "audio/webm",
            "audio/wav",
            "audio/mp4",
            "audio/ogg",
            "audio/mpeg",
            "audio/flac",
            "audio/x-wav",
        ],
        description="Allowed audio MIME types",
    )

    # ── CORS ───────────────────────────────────────────────────────────────────
    cors_origins: list[str] = Field(
        default_factory=lambda: ["*"],
        description="Allowed CORS origins",
    )

    # ── Translation ────────────────────────────────────────────────────────────
    allow_cloud_translation: bool = Field(
        False,
        description="Enable Google Translate (cloud PII risk)",
    )

    # ── Observability ─────────────────────────────────────────────────────────
    log_level: str = Field("INFO", description="Logging level")
    log_format: str = Field("json", description="Log format: json|text")

    @field_validator("api_keys", mode="before")
    @classmethod
    def parse_api_keys(cls, v):
        if isinstance(v, str):
            return [k.strip() for k in v.split(",") if k.strip()]
        return v

    @field_validator("allowed_audio_types", mode="before")
    @classmethod
    def parse_audio_types(cls, v):
        if isinstance(v, str):
            return [t.strip() for t in v.split(",") if t.strip()]
        return v

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_cors(cls, v):
        if isinstance(v, str):
            return [o.strip() for o in v.split(",") if o.strip()]
        return v

    def voice_for_lang(self, lang: str) -> str:
        """Return the default edge-tts voice for a given language code.

        Accepts both short ISO 639-1 codes (``hi``) and full BCP-47 tags
        (``hi-IN``) — the region suffix is stripped before lookup.
        """
        base = (lang or "").split("-")[0].lower()
        voices = {
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
        return voices.get(base, self.default_voice)

    @classmethod
    def settings_customise_sources(  # type: ignore[override]
        cls,
        settings_cls: Type[BaseSettings],
        init_settings: Any,
        env_settings: Any,
        dotenv_settings: Any,
        file_secret_settings: Any,
    ) -> tuple[Any, ...]:
        """Replace the DotEnv source with our comma-safe subclass."""
        safe_dotenv = _CommaSafeDotEnvSource(
            settings_cls,
            env_file=cls.model_config.get("env_file"),
            env_file_encoding=cls.model_config.get("env_file_encoding"),
            case_sensitive=cls.model_config.get("case_sensitive", False),
            env_prefix=cls.model_config.get("env_prefix", ""),
            env_ignore_empty=cls.model_config.get("env_ignore_empty", False),
            env_nested_delimiter=cls.model_config.get("env_nested_delimiter"),
        )
        return init_settings, env_settings, safe_dotenv, file_secret_settings


@lru_cache(maxsize=1)
def get_settings() -> VocalTwistSettings:
    return VocalTwistSettings()
