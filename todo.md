# VocalTwist — Future Enhancement Plan & Implementation Gaps

> **How to use this file:** Gaps are actionable defects or missing features.
> Enhancements are new capabilities that align with the project's objective
> (plug-and-play, privacy-first, self-hostable voice middleware).

---

## 🔴 Implementation Gaps (Current Code vs. README Promises)

### GAP-1 — Provider Plugin Registry Is Not Implemented
**Severity:** High  
**Location:** `backend/providers/__init__.py`, `backend/middleware.py`

The README instructs contributors to add custom STT/TTS providers by inserting
a class into `_STT_REGISTRY` in `backend/providers/__init__.py`.  No such dict
exists; the file only re-exports concrete classes.  Provider selection in
`middleware.py` uses hardcoded `if settings.stt_provider == "whisper"` /
`if settings.tts_provider == "edge_tts"` branches.

**Fix:** Add a real dict-based registry to `backend/providers/__init__.py` and
replace the if/else blocks in `middleware.py` with registry lookups.  This
unlocks the "swap STT/TTS providers without changing application code" promise.

---

### GAP-2 — Cloud Translation Feature Is Not Wired Up
**Severity:** Medium  
**Location:** `backend/config.py:60`, `backend/middleware.py` (absent)

`VOCALTWIST_ALLOW_CLOUD_TRANSLATION` exists in `VocalTwistSettings` and
`deep-translator==1.11.4` is in `requirements.txt`, but the setting is never
read by any endpoint handler.  No translation step is applied to transcripts.

**Fix:** In the `/api/transcribe` response path, if `allow_cloud_translation`
is `True` and a target language is requested, pass the transcript through
`deep_translator.GoogleTranslator`.  Add a `translated_text` field to
`TranscribeResponse`.  Document the GDPR/CCPA implication (text leaves the
local environment).

---

### GAP-3 — `faster-whisper` / `av` Version Incompatibility on Python 3.13
**Severity:** Medium  
**Location:** `requirements.txt`

`faster-whisper==1.0.3` declares `av>=11.0,<13` as a hard dependency.  On
Python 3.13, no pre-built wheel for `av<13` exists; we installed `av==17.0.0`
as a workaround.  A version incompatibility warning is raised at runtime and
may cause silent audio-decoding failures.

**Fix:** Upgrade `faster-whisper` to `>=1.2.1` which supports newer `av`
versions, and re-pin `av` accordingly in `requirements.txt`.

---

### GAP-4 — Security Tests Missing for API Key & Rate Limiting
**Severity:** Medium  
**Location:** `backend/tests/conftest.py`, `backend/tests/test_transcribe.py`

`conftest.py` explicitly disables `api_key_enabled` and `rate_limit_enabled`
for every test.  Neither security feature has any dedicated test cases.  A
regression in `security.py` would go undetected.

**Fix:** Add a separate `test_security.py` with fixtures that enable both
features, verifying:
- Requests without `X-API-Key` return 401 when enabled.
- Requests with an invalid key return 401.
- The Nth+1 request from an IP returns 429.
- `Retry-After` header is present on 429 responses.

---

### GAP-5 — In-Memory Rate Limiter Not Suitable for Multi-Worker Deployment
**Severity:** Medium  
**Location:** `backend/security.py`, `docker-compose.yml`

The `RateLimiter` class uses a Python `dict` keyed by IP.  When uvicorn is
started with `--workers N > 1`, each worker has its own dict; limits are
effectively `N × limit`.  The class itself documents this limitation.
`docker-compose.yml` has no Redis service.

**Fix:**
1. Add an optional Redis-backed limiter using `slowapi` or `limits[redis]`.
2. Add a `redis` service to `docker-compose.yml` and a
   `VOCALTWIST_REDIS_URL` config option.
3. Fall back to the in-memory limiter when Redis is unavailable.

---

### GAP-6 — No Server-Side LLM Readiness Probe
**Severity:** Low  
**Location:** `VocalTwistTest/app.py`

LM Studio health is polled client-side (JavaScript, every 10 s) but the
`/api/health` endpoint always returns `"status": "ok"` regardless of whether
the LLM backend is reachable.  This makes `/api/health` unsuitable as a
container readiness probe.

**Fix:** In the lifespan startup, perform a one-shot HTTP probe to
`LM_STUDIO_URL`.  Expose an `llm_available: bool` field in `HealthResponse`
and set the HTTP status to `503` when the LLM is unreachable, so container
orchestrators can delay traffic routing.

---

### GAP-7 — `voice_for_lang()` Language Map Is Incomplete
**Severity:** Low  
**Location:** `backend/config.py:92–104`

Only 10 languages are mapped.  Edge TTS supports 100+ locales.  Any language
not in the dict silently falls back to the global default voice
(`en-US-AriaNeural`), which may speak the wrong language.

**Fix:** Generate the map dynamically from `AVAILABLE_VOICES` in
`edge_tts_provider.py`, picking the first female or default voice per BCP-47
language prefix, or expand the static map to cover at least the top 30
languages.

---

## 🟡 Near-Term Enhancements (High Value, Low Complexity)

### ENH-1 — Streaming TTS (Chunked HTTP Response)
**Priority:** High  
**Effort:** Medium

`/api/speak` collects all MP3 bytes in memory before responding.  For long
texts (>300 chars) this adds 2–4 s of latency before playback starts.

Implement a `StreamingResponse` variant that yields edge-tts audio chunks as
they are synthesised.  Add a `stream=true` query parameter to opt in.
Frontend `VocalTwistChatbot` can use `MediaSource` or chunked fetch to begin
playback immediately.

---

### ENH-2 — Streaming LLM Response (Server-Sent Events)
**Priority:** High  
**Effort:** Medium

`/api/chat` waits for the full LLM response before returning.  Streaming
responses (`stream: true` in LM Studio API) would allow the frontend to
display tokens as they arrive and trigger TTS on sentence-complete events.

Implement an `/api/chat/stream` endpoint using `StreamingResponse` with
`text/event-stream`.  The frontend chatbot can handle `vt:token` events to
update the chat bubble progressively.

---

### ENH-3 — Conversation Session Memory
**Priority:** High  
**Effort:** Low–Medium

The demo chatbot reconstructs the full conversation from the client-sent
`messages` array on every request.  There is no server-side session storage.
This means:
- Long conversations balloon the request payload.
- Context can be silently truncated at the LLM context window.
- No cross-tab / multi-device continuity.

Implement an optional in-memory (or Redis-backed) session store keyed by a
`session_id` cookie/header.  Add a `GET /api/chat/history` and
`DELETE /api/chat/history` endpoint pair for retrieval and GDPR erasure
compliance.

---

### ENH-4 — WebSocket Real-Time STT
**Priority:** Medium  
**Effort:** High

Current push-to-talk records a complete audio buffer, then POSTs it.  A
WebSocket endpoint (`/ws/transcribe`) would allow the server to stream partial
transcripts as audio chunks arrive, enabling live captions and faster response
in ambient mode.

Use faster-whisper's segment generator with short audio windows and push
partial results over the WebSocket connection.

---

### ENH-5 — GPU / CUDA Setup Guide & Validation Endpoint
**Priority:** Medium  
**Effort:** Low

`VOCALTWIST_WHISPER_DEVICE=cuda` is a supported config option but:
- No documentation on required CUDA / cuDNN versions.
- No diagnostic to check whether CUDA is available at startup.
- No CI matrix for GPU.

Add a startup log message (or `/api/health` field) reporting the active
compute device and ctranslate2 backend.  Add a `docs/gpu-setup.md` guide.

---

### ENH-6 — Dynamic Voice Discovery
**Priority:** Medium  
**Effort:** Low

`AVAILABLE_VOICES` in `edge_tts_provider.py` is a static list compiled at
development time.  Microsoft adds new voices periodically.

Implement a cached `edge_tts.list_voices()` call on startup and refresh the
list daily.  This keeps `/api/voices` up-to-date without a package update.

---

### ENH-7 — Audio Magic-Byte Validation Should Be Enforced
**Priority:** Medium  
**Effort:** Low

In `backend/security.py:142`, the magic-byte check logs a warning but
deliberately passes the request through:

```python
# Warn but do NOT block — some valid containers are not in our list.
```

This is intentionally fail-open to avoid false positives, but it means
a crafted file with a valid MIME type but wrong bytes will be processed.  The
approach is reasonable but should be documented as a deliberate design choice,
and the MIME allow-list should be tightened where feasible.

Consider adding a `VOCALTWIST_STRICT_AUDIO_VALIDATION=false` config option
that, when enabled, treats the magic-byte check as fatal.

---

## 🟢 Longer-Term Enhancements

### ENH-8 — Alternative STT Providers
- **Whisper.cpp** (CPU-only, no Python bindings overhead)
- **OpenAI Whisper API** (cloud fallback, optional)
- **Vosk** (offline, low-resource devices)

The provider registry (GAP-1 fix) is a prerequisite.

---

### ENH-9 — Alternative TTS Providers
- **Coqui TTS / XTTS** (local, voice-cloning)
- **Piper TTS** (ultra-fast, offline, embedded-device friendly)
- **ElevenLabs API** (premium quality, optional cloud)

---

### ENH-10 — Silence / Noise Detection on Server Side
The browser-based Silero VAD is robust but requires ONNX runtime loaded in the
browser (~5 MB).  Expose a server-side VAD-only endpoint
(`POST /api/detect-speech`) that returns `{has_speech: bool, speech_ratio: float}`
so clients without ONNX support (e.g., mobile apps, CLI tools) can benefit from
silence filtering.

---

### ENH-11 — Metrics & Observability (Prometheus)
Expose a `/metrics` endpoint (via `prometheus-fastapi-instrumentator`) with:
- `vt_transcribe_duration_seconds` histogram
- `vt_speak_duration_seconds` histogram
- `vt_transcribe_total` / `vt_speak_total` counters
- `vt_rate_limit_rejections_total` counter

Add a Grafana dashboard JSON to `docs/grafana/`.

---

### ENH-12 — Frontend Jest Test Coverage Gaps
`frontend/tests/vocal-twist.test.js` exists but the CI workflow only runs it
`if frontend/tests/package.json exists`.  Run `npm test` in CI regardless and
report coverage.  Missing test scenarios:
- `AmbientVAD` start/stop/flush lifecycle
- `VocalTwistElement` web component attribute reflection
- Error state UI rendering

---

### ENH-13 — Browser Extension / PWA
Package the `<vocal-twist>` web component as a browser extension or PWA that
injects a voice input overlay into any web page, forwarding transcripts to the
user's self-hosted VocalTwist instance.

---

### ENH-14 — Rate Limiter Per API-Key (Not Just Per IP)
When API keys are enabled, rate limits should be per-key (not per-IP) so that
a shared NAT network doesn't cause unintended throttling for legitimate users.

---

## 🔒 Security & Compliance TODOs

| ID | Item | Priority |
|----|------|----------|
| SEC-1 | Add automated `pip-audit` / Dependabot to CI for dependency CVE scanning | High |
| SEC-2 | Pin GitHub Actions to commit SHAs (not `@v3` tags) in `.github/workflows/ci.yml` | High |
| SEC-3 | Add `VOCALTWIST_SECRET_KEY` for HMAC-signed session tokens (if session store added) | Medium |
| SEC-4 | Enforce `read-only` filesystem in Docker for model serving container | Medium |
| SEC-5 | Add `trivy` image scan to CI/CD pipeline (README mentions it but CI does not run it) | Medium |
| SEC-6 | Document `X-Forwarded-For` trust: only trust the header from known proxy IPs | Low |
| SEC-7 | Consider adding response-body scrubbing for accidental PII in transcripts before logging | Low |

---

## 📋 Dependency Maintenance

| Package | Issue |
|---------|-------|
| `faster-whisper==1.0.3` | Outdated; requires `av<13` (incompatible with Python 3.13 pre-builts). Upgrade to `>=1.2.1`. |
| `av` | Must be loosened in requirements once faster-whisper is updated. |
| `pydantic==2.9.2` | Pydantic 2.10+ released; review changelog for breaking changes before upgrading. |
| `edge-tts==6.1.12` | Stable; Microsoft may change the endpoint — add retry logic for transient failures. |
| `ruff==0.6.9` | Pin to latest for lint consistency across contributors. |
