"""VocalTwistTest — Demo Chatbot FastAPI Application.

Mounts the VocalTwist voice middleware backend and adds an LLM chat endpoint
backed by LM Studio (mistralai/ministral-3-3b by default).
"""
from __future__ import annotations

import collections
import logging
import os
import sys
import time
from contextlib import asynccontextmanager
from typing import AsyncIterator

import httpx
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, field_validator

# ── Path bootstrap — makes `backend` importable without installing it ──────────
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend import create_app  # noqa: F401 – re-exported for external callers
from backend.middleware import router as vt_router

# ── Configuration ──────────────────────────────────────────────────────────────
LM_STUDIO_URL: str = os.getenv(
    "LM_STUDIO_URL", "http://localhost:1234/v1/chat/completions"
)
LM_STUDIO_MODEL: str = os.getenv("LM_STUDIO_MODEL", "mistralai/ministral-3-3b")

_SYSTEM_PROMPT_TEMPLATE = (
    "You are a helpful voice assistant. Be concise and conversational. "
    "You MUST respond exclusively in {language_name}. "
    "Do NOT switch to any other language under any circumstances."
)

# ISO 639-1 code → full language name for LLM prompt
_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English",
    "hi": "Hindi",
    "mr": "Marathi",
    "es": "Spanish",
    "fr": "French",
    "pt": "Portuguese",
    "de": "German",
    "zh": "Chinese (Mandarin)",
    "ja": "Japanese",
    "ar": "Arabic",
}

# Rate limiting: 10 chat requests / 60 s per IP
_RATE_LIMIT = 10
_RATE_WINDOW = 60  # seconds

# Circuit breaker: open after 3 consecutive LLM failures; reset after 60 s
_CB_THRESHOLD = 3
_CB_RESET_SECS = 60.0

_EMERGENCY_KEYWORDS: frozenset[str] = frozenset(
    {
        "emergency", "911", "ambulance", "fire", "police",
        "dying", "dead", "suicide", "overdose", "attack", "danger",
        "sos", "urgent", "critical", "help me", "call for help",
    }
)

# Static/frontend directories
_DEMO_DIR = os.path.dirname(os.path.abspath(__file__))
_STATIC_DIR = os.path.join(_DEMO_DIR, "static")
_FRONTEND_DIR = os.path.join(os.path.dirname(_DEMO_DIR), "frontend")

# ── In-memory rate limiter ─────────────────────────────────────────────────────
_rate_store: dict[str, list[float]] = collections.defaultdict(list)


def _check_rate_limit(ip: str) -> bool:
    """Return True if the request from *ip* is within the allowed rate."""
    now = time.monotonic()
    window_start = now - _RATE_WINDOW
    history = [t for t in _rate_store[ip] if t > window_start]
    if len(history) >= _RATE_LIMIT:
        _rate_store[ip] = history
        return False
    history.append(now)
    _rate_store[ip] = history
    return True


# ── Circuit breaker state ──────────────────────────────────────────────────────
_cb_failures: int = 0
_cb_last_failure: float = 0.0


def _cb_allow() -> bool:
    if _cb_failures >= _CB_THRESHOLD:
        if time.monotonic() - _cb_last_failure > _CB_RESET_SECS:
            # Half-open: allow one probe through
            return True
        return False
    return True


def _cb_record_failure() -> None:
    global _cb_failures, _cb_last_failure
    _cb_failures += 1
    _cb_last_failure = time.monotonic()


def _cb_record_success() -> None:
    global _cb_failures
    _cb_failures = 0


# ── HTTP client lifecycle ──────────────────────────────────────────────────────
_http_client: httpx.AsyncClient | None = None

_log = logging.getLogger("vocaltwist.demo")


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:  # noqa: ARG001
    global _http_client
    _log.info(
        "VocalTwistTest starting — LM Studio: %s  model: %s",
        LM_STUDIO_URL,
        LM_STUDIO_MODEL,
    )
    _http_client = httpx.AsyncClient(timeout=60.0)
    yield
    await _http_client.aclose()
    _log.info("VocalTwistTest shutdown complete")


# ── FastAPI application ────────────────────────────────────────────────────────
app = FastAPI(
    title="VocalTwistTest — Demo Chatbot",
    description=(
        "Demo chatbot combining VocalTwist voice middleware with an "
        "LM Studio LLM (mistralai/ministral-3-3b)."
    ),
    version="1.0.0",
    lifespan=_lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # Demo only — tighten for production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include VocalTwist router — provides /api/transcribe, /api/speak, etc.
app.include_router(vt_router)


# ── Pydantic models ────────────────────────────────────────────────────────────
class ChatMessage(BaseModel):
    role: str
    content: str

    @field_validator("role")
    @classmethod
    def _validate_role(cls, v: str) -> str:
        if v not in {"user", "assistant", "system"}:
            raise ValueError("role must be user, assistant, or system")
        return v


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    language: str = "en"

    @field_validator("messages")
    @classmethod
    def _not_empty(cls, v: list[ChatMessage]) -> list[ChatMessage]:
        if not v:
            raise ValueError("messages must not be empty")
        return v


class ChatResponse(BaseModel):
    reply: str
    is_emergency: bool


# ── /api/chat ──────────────────────────────────────────────────────────────────
@app.post(
    "/api/chat",
    response_model=ChatResponse,
    summary="Send a chat message to the LLM",
    tags=["Demo"],
)
async def chat(request: Request, body: ChatRequest) -> ChatResponse:
    """Forward a conversation to LM Studio and return the assistant reply.

    - Enforces per-IP rate limiting (10 req/min).
    - Uses a circuit breaker to fail fast when LM Studio is unreachable.
    - Detects emergency keywords in the response.
    """
    client_ip: str = (request.client.host if request.client else "unknown")

    if not _check_rate_limit(client_ip):
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Rate limit exceeded. Please wait before sending another message.",
        )

    if not _cb_allow():
        _log.warning("Circuit breaker open — returning fallback reply")
        return ChatResponse(
            reply=(
                "I'm having trouble reaching the AI service right now. "
                "Please ensure LM Studio is running and try again in a moment."
            ),
            is_emergency=False,
        )

    language_name = _LANGUAGE_NAMES.get(body.language, body.language)
    system_prompt = _SYSTEM_PROMPT_TEMPLATE.format(language_name=language_name)
    payload: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    payload.extend({"role": m.role, "content": m.content} for m in body.messages)

    assert _http_client is not None, "HTTP client not initialised"

    try:
        resp = await _http_client.post(
            LM_STUDIO_URL,
            json={
                "model": LM_STUDIO_MODEL,
                "messages": payload,
                "max_tokens": 512,
                "temperature": 0.7,
            },
        )
        resp.raise_for_status()
        data = resp.json()
        reply: str = data["choices"][0]["message"]["content"].strip()
        _cb_record_success()
    except (
        httpx.ConnectError,
        httpx.TimeoutException,
        httpx.HTTPStatusError,
        KeyError,
        IndexError,
        ValueError,
    ) as exc:
        _cb_record_failure()
        _log.warning("LLM call failed (%s): %s", type(exc).__name__, exc)
        return ChatResponse(
            reply=(
                "I'm sorry, I can't reach the AI service right now. "
                "Please ensure LM Studio is running with the "
                f"'{LM_STUDIO_MODEL}' model loaded."
            ),
            is_emergency=False,
        )

    reply_lower = reply.lower()
    is_emergency = any(kw in reply_lower for kw in _EMERGENCY_KEYWORDS)
    return ChatResponse(reply=reply, is_emergency=is_emergency)


# ── Frontend asset routes ──────────────────────────────────────────────────────
# Serve VocalTwist frontend library files directly so the demo page can load
# them from the root without bundling.

@app.get("/health", include_in_schema=False)
async def _health_alias() -> dict[str, str]:
    """Alias so the extension's background.js probe (GET /health) also works.
    
    The VocalTwist backend canonically serves /api/health. This alias lets
    the Chrome extension probe /health without a full path update.
    """
    return {"status": "ok"}


@app.get("/favicon.ico", include_in_schema=False)
async def _favicon() -> FileResponse:
    _ico = os.path.join(_STATIC_DIR, "favicon.ico")
    if os.path.exists(_ico):
        return FileResponse(_ico, media_type="image/x-icon")
    from fastapi.responses import Response
    return Response(status_code=204)



@app.get("/vocal-twist.js", include_in_schema=False)
async def _serve_vt_js() -> FileResponse:
    return FileResponse(
        os.path.join(_FRONTEND_DIR, "vocal-twist.js"),
        media_type="application/javascript",
    )


@app.get("/vocal-twist.css", include_in_schema=False)
async def _serve_vt_css() -> FileResponse:
    return FileResponse(
        os.path.join(_FRONTEND_DIR, "vocal-twist.css"),
        media_type="text/css",
    )


@app.get("/ambient-vad.js", include_in_schema=False)
async def _serve_ambient_vad() -> FileResponse:
    return FileResponse(
        os.path.join(_FRONTEND_DIR, "ambient-vad.js"),
        media_type="application/javascript",
    )


# ── Static files (must be last — catch-all mount) ─────────────────────────────
app.mount("/", StaticFiles(directory=_STATIC_DIR, html=True), name="static")
