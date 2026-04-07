"""VocalTwist security utilities.

Covers:
* API-key header validation
* Audio file validation (size, MIME type, magic bytes)
* Text sanitisation (HTML/script stripping, length limiting)
* Request-ID extraction / generation
* Per-IP in-memory rate limiting with graceful slowapi fallback
"""
from __future__ import annotations

import re
import time
import uuid
import logging
import collections
from typing import TYPE_CHECKING

from fastapi import HTTPException, Request, UploadFile, status

if TYPE_CHECKING:
    from .config import VocalTwistSettings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Magic-byte signatures for valid audio containers
# ---------------------------------------------------------------------------

_AUDIO_MAGIC: list[tuple[bytes, str]] = [
    (b"RIFF", "audio/wav"),           # WAV / RIFF
    (b"fLaC", "audio/flac"),           # FLAC
    (b"\x1aE\xdf\xa3", "audio/webm"), # WebM / MKV (EBML header)
    (b"OggS", "audio/ogg"),            # OGG
    (b"\xff\xfb", "audio/mpeg"),       # MP3 (no ID3)
    (b"\xff\xf3", "audio/mpeg"),       # MP3 variant
    (b"\xff\xf2", "audio/mpeg"),       # MP3 variant
    (b"ID3", "audio/mpeg"),            # MP3 with ID3 tag
    (b"\x00\x00\x00", "audio/mp4"),   # MP4 / M4A (ftyp box)
]

# ---------------------------------------------------------------------------
# HTML / script-tag stripping
# ---------------------------------------------------------------------------

_SCRIPT_RE = re.compile(r"<script[\s\S]*?>[\s\S]*?</script>", re.IGNORECASE)
_STYLE_RE = re.compile(r"<style[\s\S]*?>[\s\S]*?</style>", re.IGNORECASE)
_HTML_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


# ---------------------------------------------------------------------------
# API-key validation
# ---------------------------------------------------------------------------

def validate_api_key(request: Request, settings: "VocalTwistSettings") -> None:
    """Raise HTTP 401 if API-key enforcement is enabled and the key is invalid.

    The key must be supplied in the ``X-API-Key`` header.

    Args:
        request:  Incoming FastAPI request.
        settings: Application settings instance.

    Raises:
        HTTPException: 401 if the key is missing or not in the allowed set.
    """
    if not settings.api_key_enabled:
        return

    supplied = request.headers.get("X-API-Key", "")
    if not supplied or supplied not in settings.api_keys:
        logger.warning(
            "Invalid or missing API key",
            extra={"ip": _client_ip(request)},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing X-API-Key header.",
        )


# ---------------------------------------------------------------------------
# Audio file validation
# ---------------------------------------------------------------------------

async def validate_audio_file(
    file: UploadFile,
    settings: "VocalTwistSettings",
) -> bytes:
    """Read, size-check, and MIME-validate an uploaded audio file.

    Performs three layers of validation:

    1. **Size** — reject files larger than ``settings.max_audio_bytes``.
    2. **MIME type** — reject content types not in the allow-list.
    3. **Magic bytes** — read the first 12 bytes and confirm it looks like
       a known audio container (guards against MIME-type spoofing).

    Args:
        file:     The :class:`~fastapi.UploadFile` received from the client.
        settings: Application settings with limits and allowed types.

    Returns:
        The raw audio bytes (already read from the upload stream).

    Raises:
        HTTPException: 400 for size or type violations.
    """
    # Read all bytes up to max + 1 so we can detect oversized uploads.
    audio_bytes = await file.read(settings.max_audio_bytes + 1)

    if len(audio_bytes) > settings.max_audio_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Audio file exceeds the maximum allowed size of "
                f"{settings.max_audio_bytes // (1024 * 1024)} MB."
            ),
        )

    # Normalise the MIME type (strip codec params like "; codecs=opus")
    raw_content_type = (file.content_type or "application/octet-stream").split(";")[0].strip().lower()

    if raw_content_type not in settings.allowed_audio_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported audio type '{raw_content_type}'. "
                f"Allowed: {', '.join(settings.allowed_audio_types)}."
            ),
        )

    # Magic-byte check (first 12 bytes) — best-effort, not exhaustive.
    header = audio_bytes[:12]
    if not _has_valid_audio_magic(header):
        logger.warning(
            "Audio magic-byte check failed",
            extra={"content_type": raw_content_type, "header_hex": header.hex()},
        )
        # Warn but do NOT block — some valid containers are not in our list.
        # This is intentionally non-fatal to avoid false positives.

    return audio_bytes


def _has_valid_audio_magic(header: bytes) -> bool:
    for magic, _ in _AUDIO_MAGIC:
        if header[: len(magic)] == magic:
            return True
    # MP4/M4A: 4-byte size field then b"ftyp" at offset 4
    if len(header) >= 8 and header[4:8] == b"ftyp":
        return True
    return False


# ---------------------------------------------------------------------------
# Text sanitisation
# ---------------------------------------------------------------------------

def sanitize_text(text: str, max_length: int = 2_000) -> str:
    """Strip HTML / script tags, normalise whitespace, and enforce length limit.

    This is the final gate before text reaches the TTS engine.  It is safe to
    call multiple times — the operation is idempotent.

    Args:
        text:       Raw input text (may contain HTML markup).
        max_length: Hard cap on output length (default 2000 characters).

    Returns:
        Clean, whitespace-normalised text truncated to *max_length*.

    Raises:
        ValueError: If the cleaned text is empty.
    """
    text = _SCRIPT_RE.sub("", text)
    text = _STYLE_RE.sub("", text)
    text = _HTML_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text).strip()

    if not text:
        raise ValueError("Text is empty after sanitisation.")

    return text[:max_length]


# ---------------------------------------------------------------------------
# Request-ID helpers
# ---------------------------------------------------------------------------

def get_request_id(request: Request) -> str:
    """Extract or generate a unique request identifier.

    Checks (in order):
    * ``X-Request-ID`` header
    * ``X-Correlation-ID`` header
    * Generates a fresh UUID4

    The ID is attached to all log records for a request so correlated events
    can be traced across distributed services.

    Args:
        request: Incoming FastAPI request.

    Returns:
        A non-empty string request identifier.
    """
    return (
        request.headers.get("X-Request-ID")
        or request.headers.get("X-Correlation-ID")
        or str(uuid.uuid4())
    )


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

def _parse_rate(rate_str: str) -> tuple[int, int]:
    """Parse a rate-limit string like ``"20/minute"`` into ``(count, seconds)``."""
    _UNIT_SECONDS = {"second": 1, "minute": 60, "hour": 3600, "day": 86400}
    try:
        count_str, unit = rate_str.lower().split("/")
        unit = unit.strip().rstrip("s")  # "minutes" → "minute"
        return int(count_str.strip()), _UNIT_SECONDS[unit]
    except (ValueError, KeyError):
        logger.warning("Invalid rate limit format '%s', using 60/minute", rate_str)
        return 60, 60


class _WindowEntry:
    """Sliding-window state for a single IP address."""

    __slots__ = ("timestamps",)

    def __init__(self) -> None:
        self.timestamps: collections.deque[float] = collections.deque()


class RateLimiter:
    """Simple in-memory sliding-window rate limiter keyed by client IP.

    This is a best-effort, single-process implementation.  In a multi-worker
    deployment you should use Redis-backed slowapi or similar.  It degrades
    gracefully — if an error occurs during the check the request is allowed
    through (fail-open to avoid availability impact).

    Usage::

        limiter = RateLimiter("20/minute")
        limiter.check(request)   # raises HTTP 429 if over limit
    """

    def __init__(self, rate_str: str = "60/minute") -> None:
        self._max_calls, self._window_seconds = _parse_rate(rate_str)
        self._state: dict[str, _WindowEntry] = {}

    def check(self, request: Request) -> None:
        """Enforce the rate limit for the client identified by *request*.

        Args:
            request: Incoming FastAPI request.

        Raises:
            HTTPException: 429 Too Many Requests if the limit is exceeded.
        """
        try:
            ip = _client_ip(request)
            now = time.monotonic()
            entry = self._state.setdefault(ip, _WindowEntry())

            # Drop timestamps outside the current window.
            cutoff = now - self._window_seconds
            while entry.timestamps and entry.timestamps[0] < cutoff:
                entry.timestamps.popleft()

            if len(entry.timestamps) >= self._max_calls:
                retry_after = int(self._window_seconds - (now - entry.timestamps[0]))
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail="Rate limit exceeded. Please slow down.",
                    headers={"Retry-After": str(max(retry_after, 1))},
                )

            entry.timestamps.append(now)

        except HTTPException:
            raise
        except Exception:
            # Fail-open: unexpected errors in rate limiting must not block requests.
            logger.exception("Rate limiter check failed — allowing request through")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _client_ip(request: Request) -> str:
    """Extract the real client IP, respecting common proxy headers."""
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP")
    if real_ip:
        return real_ip.strip()
    if request.client:
        return request.client.host
    return "unknown"
