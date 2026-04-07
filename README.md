# VocalTwist — Plug-and-Play Voice Middleware

[![CI](https://github.com/phoenice-labs/vocaltwist/actions/workflows/ci.yml/badge.svg)](https://github.com/phoenice-labs/vocaltwist/actions/workflows/ci.yml)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

VocalTwist is a self-hostable voice middleware layer you can mount inside any
existing FastAPI application in minutes.  It exposes REST endpoints for
**speech-to-text** (Whisper), **text-to-speech** (Microsoft Edge Neural TTS),
and a ready-to-use browser JavaScript library that handles recording,
transcription, TTS playback, and always-on ambient listening — all without
any third-party cloud subscription.

The `VocalTwistTest/` directory ships a **demo chatbot** that wires VocalTwist
to an LM Studio LLM, demonstrating full-duplex voice conversations with
push-to-talk, ambient mode, automatic TTS response playback, and language
switching.

Privacy note: audio is **never persisted** to disk.  Transcription runs
entirely in-process on your own hardware.

---

## Features

- 🎙 **Push-to-talk recording** — hold a button, speak, release to transcribe
- 🌐 **Ambient (always-on) listening** — Silero VAD in the browser detects speech
  automatically; only voiced segments are sent to the server
- 🔊 **Neural TTS** — Microsoft Edge voices with zero API key required
- 🌍 **Multilingual** — language hint, auto voice selection (en, hi, es, fr, de, zh, …)
- 🔌 **Plug-in architecture** — swap STT/TTS providers without changing application code
- 🛡 **Security-first** — optional API key auth, per-endpoint rate limiting, audio
  size validation, structured PII-masked logging
- 🐳 **Docker-ready** — multi-stage Dockerfile, `docker-compose.yml` included
- ✅ **Tested** — pytest suite for backend, Jest for frontend

---

## Quick Start (5 minutes)

```bash
# 1. Clone
git clone https://github.com/your-org/vocaltwist.git
cd vocaltwist

# 2. Install Python deps
python -m venv .venv
source .venv/scripts/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Copy config (all defaults are safe for local demo)
cp .env.example .env

# 4. Start LM Studio and load mistralai/ministral-3-3b
#    (required only for the chatbot demo, not for STT/TTS endpoints)

# 5. Run the demo chatbot
uvicorn VocalTwistTest.app:app --reload --port 8000

# Open http://localhost:8000 in your browser
```

---

## Directory Structure

```
VocalTwist/
├── backend/                    # FastAPI router — STT, TTS, health endpoints
│   ├── __init__.py             # Exports: router, create_app
│   ├── config.py               # VocalTwistSettings (pydantic-settings)
│   ├── logging_config.py       # Structured JSON logging
│   ├── middleware.py           # Router definition + create_app()
│   ├── models.py               # Pydantic request/response models
│   ├── security.py             # API key auth, rate limiter, audio validation
│   ├── providers/
│   │   ├── base.py             # STTProvider / TTSProvider abstract classes
│   │   ├── whisper_provider.py # faster-whisper implementation
│   │   └── edge_tts_provider.py# edge-tts implementation
│   └── tests/                  # Backend unit tests
│
├── frontend/                   # Browser JavaScript library (zero dependencies)
│   ├── vocal-twist.js          # VocalTwistRecorder, VocalTwistTTS, VocalTwist,
│   │                           # VocalTwistElement (<vocal-twist>)
│   ├── ambient-vad.js          # AmbientVAD (always-on Silero VAD)
│   ├── vocal-twist.css         # Shadow-DOM styles for <vocal-twist>
│   └── sample-usage.html       # Integration demo
│
├── VocalTwistTest/             # Demo chatbot (VocalTwist + LM Studio)
│   ├── app.py                  # FastAPI app: mounts vt_router + /api/chat
│   ├── static/
│   │   ├── index.html          # Chat UI
│   │   └── chatbot.js          # VocalTwistChatbot class
│   └── tests/
│       └── test_chatbot.py     # Integration tests (LLM mocked)
│
├── .env.example                # All configuration options with documentation
├── .github/workflows/ci.yml    # GitHub Actions: test + lint + Docker build
├── docker-compose.yml          # Single-service compose file
├── Dockerfile                  # Multi-stage production image
├── openapi.yaml                # OpenAPI 3.1 spec for all endpoints
├── requirements.txt            # Pinned Python dependencies
└── README.md                   # You are here
```

---

## Integration

> **See [INTEGRATION.md](INTEGRATION.md) for the complete integration guide.**

INTEGRATION.md covers:
- Mounting VocalTwist inside any existing FastAPI or web application
- Push-to-talk, TTS, and ambient listening with copy-paste JavaScript snippets  
- Full REST API reference with curl, Python, and Node.js examples
- Agentic AI / man-in-the-middle voice-note patterns (LangChain tool wrappers included)
- Enabling and disabling individual features independently
- Language and voice selection (10 built-in languages, 400+ voices)
- Security, authentication, and all environment variables
- Compiled / minified distribution (see [SHIPPING.md](SHIPPING.md))

---

---

## VocalTwistTest Demo Chatbot

### Requirements

- Python 3.11+ with VocalTwist dependencies installed
- [LM Studio](https://lmstudio.ai/) running locally with `mistralai/ministral-3-3b` loaded
  - Enable the local server (default: `http://localhost:1234`)
  - The demo still works without LM Studio — it shows a friendly fallback message

### Running the demo

```bash
# Start LM Studio → load mistralai/ministral-3-3b → start server on :1234
uvicorn VocalTwistTest.app:app --reload --port 8000
# Open http://localhost:8000
```

### Demo features

| Feature | How to use |
|---|---|
| Text chat | Type a message and press Enter or ➤ |
| Push-to-talk | Hold the 🎤 mic button, speak, release |
| Ambient listening | Click **🔁 Ambient** — the mic stays open |
| Language switching | Use the dropdown (top right) |
| TTS auto-playback | Every assistant reply is spoken aloud |
| Full-duplex | Speaking interrupts the assistant's TTS |
| Emergency alert | Red banner if the reply contains emergency keywords |
| LM Studio status | Green/red dot in the header (polled every 10 s) |

---

## Adding a Custom STT/TTS Provider

### 1. Implement the base class

```python
# backend/providers/my_stt.py
from backend.providers.base import STTProvider, TranscribeResult

class MySTTProvider(STTProvider):
    """Plug your own STT engine here."""

    async def transcribe(
        self,
        audio_bytes: bytes,
        *,
        language: str | None = None,
        vad_filter: bool = False,
    ) -> TranscribeResult:
        # Call your engine...
        return TranscribeResult(
            text="hello world",
            display_text="Hello world",
            language="en",
            duration_ms=1200,
        )
```

### 2. Register the provider

```python
# backend/providers/__init__.py  (add to _STT_REGISTRY)
from .my_stt import MySTTProvider

_STT_REGISTRY = {
    "whisper": WhisperSTTProvider,
    "my_stt":  MySTTProvider,         # ← add this line
}
```

### 3. Enable via environment variable

```bash
VOCALTWIST_STT_PROVIDER=my_stt uvicorn VocalTwistTest.app:app
```

---

## Security

### API key authentication

```ini
# .env
VOCALTWIST_API_KEY_ENABLED=true
VOCALTWIST_API_KEYS=sk-live-abc123,sk-live-def456
```

Every request must then include `X-API-Key: sk-live-abc123`.

Keys should be stored in a secrets manager (HashiCorp Vault, AWS Secrets
Manager, Azure Key Vault) — never committed to source control.

### Rate limiting

Configurable per endpoint family:

```ini
VOCALTWIST_RATE_LIMIT_TRANSCRIBE=20/minute
VOCALTWIST_RATE_LIMIT_SPEAK=30/minute
```

The demo chatbot adds its own in-process rate limiter (10 req/min per IP)
on the `/api/chat` endpoint.

### Audio validation

All uploaded audio files are validated for:
- MIME type (allowlist: WebM, WAV, MP4, OGG, MPEG, FLAC)
- File size (default maximum: 10 MB)
- Non-zero content

### Privacy — no audio persistence

Audio bytes are processed entirely in memory and never written to disk.
Transcripts are logged at DEBUG level with PII masking applied.

### GDPR / CCPA notes

- Audio data is not stored beyond the duration of the HTTP request.
- Transcripts appear in server logs (DEBUG level only, disabled by default).
  If you enable DEBUG logging, ensure log retention policies comply with your
  data-handling obligations.
- The optional Google Translation feature
  (`VOCALTWIST_ALLOW_CLOUD_TRANSLATION=true`) sends transcript text to
  Google Cloud.  Review your legal obligations before enabling.

---

## Docker Deployment

```bash
# Build and start
docker compose up --build

# Or pull and run directly
docker run -p 8000:8000 \
  -e LM_STUDIO_URL=http://host.docker.internal:1234/v1/chat/completions \
  vocaltwist:latest
```

The container runs as a non-root user (`vocaltwist`) and uses
`python:3.11-slim` as the base image to minimise the attack surface.

**Production checklist:**
- [ ] Set `VOCALTWIST_API_KEY_ENABLED=true` and rotate keys regularly
- [ ] Replace `VOCALTWIST_CORS_ORIGINS=*` with your actual origin(s)
- [ ] Mount a log volume and configure a retention policy
- [ ] Pin Docker images by digest (not `:latest`) in production manifests
- [ ] Run a vulnerability scan (`trivy image vocaltwist:latest`) before deploying

---

## CI/CD

The GitHub Actions workflow (`.github/workflows/ci.yml`) runs on every push
to `main` / `develop` and on pull requests:

1. **Backend tests** — `pytest backend/tests/ VocalTwistTest/tests/`
2. **Lint** — `ruff check backend/ VocalTwistTest/`
3. **Frontend tests** — Jest (if `frontend/tests/package.json` exists)
4. **Docker build smoke test** — builds the image but does not push

For production deployments, add a `docker/login-action` step and push to
your registry after the tests pass.

---

## Testing

```bash
# Backend unit tests
pytest backend/tests/ -v

# VocalTwistTest integration tests (LLM is mocked)
pytest VocalTwistTest/tests/ -v

# All tests
pytest -v

# Lint
ruff check backend/ VocalTwistTest/
```

---

## Contributing

1. Fork the repository and create a feature branch.
2. Install dev dependencies: `pip install -r requirements.txt`
3. Make your changes, add tests, ensure `pytest` and `ruff check` pass.
4. Open a pull request against `develop`.

Please follow the existing code style (type hints, `from __future__ import
annotations`, docstrings on public APIs).

---

## License

MIT License — see [LICENSE](LICENSE) for details.
