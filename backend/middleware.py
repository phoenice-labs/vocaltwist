"""VocalTwist main API router and application factory.

Endpoints
---------
POST /api/transcribe           — Audio file → TranscribeResponse JSON
POST /api/transcribe-ambient   — Same but VAD-forced (AmbientVAD compatibility)
POST /api/speak                — JSON text → MP3 audio bytes
GET  /api/health               — Health check
GET  /api/providers            — List registered STT/TTS providers
GET  /api/voices               — Available voices per language
"""
from __future__ import annotations

import time
from functools import lru_cache
from typing import Annotated

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import VocalTwistSettings, get_settings
from .logging_config import get_logger, setup_logging
from .models import (
    AmbientTranscribeResponse,
    ErrorResponse,
    HealthResponse,
    ProvidersResponse,
    SpeakRequest,
    TranscribeResponse,
    VoiceInfo,
    VoicesResponse,
)
from .security import (
    RateLimiter,
    get_request_id,
    sanitize_text,
    validate_api_key,
    validate_audio_file,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Application start time (used by /api/health)
# ---------------------------------------------------------------------------
_START_TIME = time.monotonic()

# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------
__version__ = "0.1.0"

# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------
router = APIRouter(prefix="/api", tags=["VocalTwist"])

# ---------------------------------------------------------------------------
# Rate limiters (one per endpoint group)
# ---------------------------------------------------------------------------
# Instantiated lazily from settings in get_transcribe_limiter / get_speak_limiter
_transcribe_limiter: RateLimiter | None = None
_speak_limiter: RateLimiter | None = None


def _get_transcribe_limiter(settings: VocalTwistSettings) -> RateLimiter | None:
    global _transcribe_limiter
    if not settings.rate_limit_enabled:
        return None
    if _transcribe_limiter is None:
        _transcribe_limiter = RateLimiter(settings.rate_limit_transcribe)
    return _transcribe_limiter


def _get_speak_limiter(settings: VocalTwistSettings) -> RateLimiter | None:
    global _speak_limiter
    if not settings.rate_limit_enabled:
        return None
    if _speak_limiter is None:
        _speak_limiter = RateLimiter(settings.rate_limit_speak)
    return _speak_limiter


# ---------------------------------------------------------------------------
# Provider helpers
# ---------------------------------------------------------------------------

def _build_stt_provider(settings: VocalTwistSettings):
    """Instantiate the configured STT provider."""
    from .providers import WhisperSTTProvider  # local import avoids circular dep

    if settings.stt_provider == "whisper":
        return WhisperSTTProvider(
            model_size=settings.whisper_model,
            device=settings.whisper_device,
            compute_type=settings.whisper_compute_type,
        )
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"STT provider '{settings.stt_provider}' is not registered.",
    )


def _build_tts_provider(settings: VocalTwistSettings):
    """Instantiate the configured TTS provider."""
    from .providers import EdgeTTSProvider  # local import avoids circular dep

    if settings.tts_provider == "edge_tts":
        return EdgeTTSProvider(default_voice=settings.default_voice)
    raise HTTPException(
        status_code=status.HTTP_501_NOT_IMPLEMENTED,
        detail=f"TTS provider '{settings.tts_provider}' is not registered.",
    )


# ---------------------------------------------------------------------------
# POST /api/transcribe
# ---------------------------------------------------------------------------

@router.post(
    "/transcribe",
    response_model=TranscribeResponse,
    summary="Transcribe an audio file to text",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid audio file"},
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "STT provider error"},
    },
)
async def transcribe(
    request: Request,
    audio: UploadFile = File(..., description="Audio file to transcribe"),
    language: Annotated[
        str | None,
        Query(description="ISO 639-1 language code hint (e.g. 'en', 'hi'). None = auto-detect."),
    ] = None,
    task: Annotated[
        str,
        Query(description="'transcribe' (same language) or 'translate' (→ English)"),
    ] = "transcribe",
    vad_filter: Annotated[
        bool,
        Query(description="Apply Silero VAD pre-filter to strip silence"),
    ] = True,
    settings: VocalTwistSettings = Depends(get_settings),
) -> TranscribeResponse:
    """Transcribe an audio file to text using the configured STT provider.

    Accepts multipart/form-data with an ``audio`` file field.  Returns a JSON
    object with the transcript, detected language, and processing duration.
    """
    request_id = get_request_id(request)
    validate_api_key(request, settings)
    limiter = _get_transcribe_limiter(settings)
    if limiter:
        limiter.check(request)

    if task not in ("transcribe", "translate"):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="task must be 'transcribe' or 'translate'.",
        )

    audio_bytes = await validate_audio_file(audio, settings)
    content_type = (audio.content_type or "audio/webm").split(";")[0].strip()

    # Normalise BCP-47 tags (e.g. 'hi-IN') to ISO 639-1 short codes ('hi')
    # that Whisper and the voice-lookup maps expect.
    if language:
        language = language.split("-")[0].lower()

    logger.info(
        "Transcribe request received",
        extra={
            "request_id": request_id,
            "bytes": len(audio_bytes),
            "content_type": content_type,
            "language": language,
            "task": task,
            "vad_filter": vad_filter,
        },
    )

    stt = _build_stt_provider(settings)
    t0 = time.monotonic()

    try:
        text = await stt.transcribe(
            audio_bytes,
            content_type=content_type,
            task=task,
            language=language,
            vad_filter=vad_filter,
        )
    except RuntimeError as exc:
        logger.exception(
            "STT provider error",
            extra={"request_id": request_id, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Speech-to-text transcription failed. Please try again.",
        ) from exc

    duration_ms = (time.monotonic() - t0) * 1000

    logger.info(
        "Transcribe complete",
        extra={
            "request_id": request_id,
            "duration_ms": round(duration_ms, 1),
            "chars": len(text),
            "language": language,
        },
    )

    display_text = _to_display_text(text)
    return TranscribeResponse(
        text=text,
        display_text=display_text,
        language=language,
        duration_ms=round(duration_ms, 1),
    )


# ---------------------------------------------------------------------------
# POST /api/transcribe-ambient
# ---------------------------------------------------------------------------

@router.post(
    "/transcribe-ambient",
    response_model=AmbientTranscribeResponse,
    summary="Transcribe ambient audio (VAD always enabled)",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid audio file"},
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
    },
)
async def transcribe_ambient(
    request: Request,
    audio: UploadFile = File(..., description="Ambient audio buffer from AmbientVAD"),
    language: Annotated[str | None, Query()] = None,
    settings: VocalTwistSettings = Depends(get_settings),
) -> AmbientTranscribeResponse:
    """Transcribe an ambient audio buffer.

    Identical to ``/api/transcribe`` but *vad_filter* is forced to ``True``
    and *task* is always ``"transcribe"``.  Designed for continuous ambient
    recording where silence removal is mandatory.
    """
    request_id = get_request_id(request)
    validate_api_key(request, settings)
    limiter = _get_transcribe_limiter(settings)
    if limiter:
        limiter.check(request)

    audio_bytes = await validate_audio_file(audio, settings)
    content_type = (audio.content_type or "audio/webm").split(";")[0].strip()

    stt = _build_stt_provider(settings)

    try:
        text = await stt.transcribe(
            audio_bytes,
            content_type=content_type,
            task="transcribe",
            language=language,
            vad_filter=True,  # always forced
        )
    except RuntimeError as exc:
        logger.exception("Ambient transcribe error", extra={"request_id": request_id})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Ambient transcription failed.",
        ) from exc

    return AmbientTranscribeResponse(
        text=text,
        display_text=_to_display_text(text),
    )


# ---------------------------------------------------------------------------
# POST /api/speak
# ---------------------------------------------------------------------------

@router.post(
    "/speak",
    summary="Synthesise text to MP3 audio",
    response_class=Response,
    responses={
        200: {
            "content": {"audio/mpeg": {}},
            "description": "MP3 audio bytes",
        },
        400: {"model": ErrorResponse, "description": "Text too long or empty"},
        401: {"model": ErrorResponse, "description": "Invalid or missing API key"},
        429: {"model": ErrorResponse, "description": "Rate limit exceeded"},
        500: {"model": ErrorResponse, "description": "TTS provider error"},
    },
)
async def speak(
    request: Request,
    body: SpeakRequest,
    settings: VocalTwistSettings = Depends(get_settings),
) -> Response:
    """Convert text to MP3 audio using the configured TTS provider.

    Returns raw ``audio/mpeg`` bytes.  The ``Content-Disposition`` header is
    set to ``inline`` so browsers can play the audio directly.
    """
    request_id = get_request_id(request)
    validate_api_key(request, settings)
    limiter = _get_speak_limiter(settings)
    if limiter:
        limiter.check(request)

    # Resolve voice: explicit → language default → global default.
    # Guard: only accept edge-tts compatible voice names (end with "Neural").
    # Browser Web Speech API voice URIs (e.g. "Google हिन्दी (hi-IN)") are
    # incompatible with edge-tts — silently ignore them and fall back to the
    # language-based default so TTS always succeeds.
    import re as _re  # noqa: PLC0415
    raw_voice = body.voice
    if raw_voice and not _re.search(r"Neural$", raw_voice, _re.IGNORECASE):
        logger.debug(
            "Ignoring incompatible voice name, using language default",
            extra={"voice": raw_voice, "language": body.language},
        )
        raw_voice = None
    voice = raw_voice or settings.voice_for_lang(body.language or settings.default_language)

    logger.info(
        "Speak request received",
        extra={
            "request_id": request_id,
            "chars": len(body.text),
            "voice": voice,
            "language": body.language,
        },
    )

    tts = _build_tts_provider(settings)
    t0 = time.monotonic()

    try:
        audio_bytes = await tts.speak(body.text, voice=voice, language=body.language)
    except RuntimeError as exc:
        logger.exception(
            "TTS provider error",
            extra={"request_id": request_id, "error": str(exc)},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Text-to-speech synthesis failed. Please try again.",
        ) from exc

    duration_ms = (time.monotonic() - t0) * 1000
    logger.info(
        "Speak complete",
        extra={
            "request_id": request_id,
            "duration_ms": round(duration_ms, 1),
            "audio_bytes": len(audio_bytes),
            "voice": voice,
        },
    )

    return Response(
        content=audio_bytes,
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": "inline; filename=speech.mp3",
            "X-Request-ID": request_id,
            "X-Duration-Ms": str(round(duration_ms, 1)),
        },
    )


# ---------------------------------------------------------------------------
# GET /api/health
# ---------------------------------------------------------------------------

@router.get(
    "/health",
    response_model=HealthResponse,
    summary="Health check",
)
async def health(
    settings: VocalTwistSettings = Depends(get_settings),
) -> HealthResponse:
    """Return a liveness / readiness summary for the VocalTwist middleware."""
    return HealthResponse(
        status="ok",
        version=__version__,
        stt_provider=settings.stt_provider,
        tts_provider=settings.tts_provider,
        uptime_s=round(time.monotonic() - _START_TIME, 2),
    )


# ---------------------------------------------------------------------------
# GET /api/providers
# ---------------------------------------------------------------------------

@router.get(
    "/providers",
    response_model=ProvidersResponse,
    summary="List available STT and TTS providers",
)
async def providers() -> ProvidersResponse:
    """Return the names of all registered and available STT/TTS providers."""
    from .providers import WhisperSTTProvider, EdgeTTSProvider  # local import

    stt_available = [WhisperSTTProvider.name] if WhisperSTTProvider.is_available() else []
    tts_available = [EdgeTTSProvider.name] if EdgeTTSProvider.is_available() else []

    return ProvidersResponse(stt=stt_available, tts=tts_available)


# ---------------------------------------------------------------------------
# GET /api/voices
# ---------------------------------------------------------------------------

@router.get(
    "/voices",
    response_model=VoicesResponse,
    summary="List available TTS voices grouped by language",
)
async def voices() -> VoicesResponse:
    """Return all TTS voices bundled with the active provider, grouped by language."""
    from .providers.edge_tts_provider import AVAILABLE_VOICES  # local import

    grouped: dict[str, list[VoiceInfo]] = {}
    for v in AVAILABLE_VOICES:
        lang = v["language"]
        info = VoiceInfo(
            name=v["name"],
            language=lang,
            gender=v.get("gender"),
        )
        grouped.setdefault(lang, []).append(info)

    return VoicesResponse(voices=grouped)


# ---------------------------------------------------------------------------
# Application factory
# ---------------------------------------------------------------------------

def create_app(settings: VocalTwistSettings | None = None) -> FastAPI:
    """Create and return a standalone FastAPI application with VocalTwist mounted.

    Args:
        settings: Optional settings override (useful in tests).  When omitted
                  the settings are loaded from environment / ``.env``.

    Returns:
        A fully configured :class:`~fastapi.FastAPI` instance ready to serve.
    """
    cfg = settings or get_settings()

    # Configure logging as early as possible.
    setup_logging(level=cfg.log_level, fmt=cfg.log_format)

    app = FastAPI(
        title="VocalTwist",
        description=(
            "Plug-and-play voice middleware for FastAPI — "
            "offline STT (faster-whisper) + neural TTS (edge-tts)."
        ),
        version=__version__,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── CORS ──────────────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── Routers ───────────────────────────────────────────────────────────────
    app.include_router(router)

    # ── Exception handlers ────────────────────────────────────────────────────
    @app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException):
        request_id = get_request_id(request)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.detail, "request_id": request_id},
            headers=getattr(exc, "headers", None) or {},
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        request_id = get_request_id(request)
        logger.exception(
            "Unhandled exception",
            extra={"request_id": request_id, "path": str(request.url)},
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "detail": "An unexpected error occurred.",
                "request_id": request_id,
            },
        )

    return app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_display_text(text: str) -> str:
    """Capitalise the first character of each sentence for display purposes."""
    if not text:
        return text
    sentences = text.split(". ")
    return ". ".join(s[:1].upper() + s[1:] for s in sentences)
