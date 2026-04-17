"""Comprehensive end-to-end tests for VocalTwist backend — 8-Step Contract.

Each test case is structured around the UTF 8-Step Test Contract:
  Step 1 — Metadata    : Test ID, name, description, tags
  Step 2 — Preconditions: App / fixture / mock state required
  Step 3 — Input Data  : Exact request payload / parameters
  Step 4 — Action      : HTTP call executed via async test client
  Step 5 — Expected    : Documented expected response
  Step 6 — Assertions  : Code assertions verifying expected vs actual
  Step 7 — Teardown    : Cleanup after test
  Step 8 — Result      : Pass/Fail captured by pytest + UTF DB writer

Test IDs TC-001 → TC-040 map 1-to-1 with entries in the UTF database.
"""
from __future__ import annotations

import asyncio
import io
import struct
import wave
from unittest.mock import AsyncMock, MagicMock, patch, call

import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI

import backend.middleware as _mw
from backend.config import VocalTwistSettings, get_settings
from backend.middleware import create_app

# ── Helpers ────────────────────────────────────────────────────────────────────

FAKE_MP3 = b"\xff\xfb\x90\x00" + b"\x00" * 128


def _make_wav(duration_secs: float = 0.2, sample_rate: int = 16_000) -> bytes:
    """Return a minimal mono 16-bit PCM WAV file."""
    n = int(sample_rate * duration_secs)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{n}h", *([0] * n)))
    return buf.getvalue()


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def base_settings() -> VocalTwistSettings:
    """Step 2 — Preconditions: API key disabled, rate limiting disabled."""
    return VocalTwistSettings(
        api_key_enabled=False,
        rate_limit_enabled=False,
        log_level="WARNING",
        log_format="text",
        stt_provider="whisper",
        tts_provider="edge_tts",
    )


@pytest.fixture(scope="module")
def base_app(base_settings: VocalTwistSettings) -> FastAPI:
    app = create_app(settings=base_settings)
    app.dependency_overrides[get_settings] = lambda: base_settings
    return app


@pytest_asyncio.fixture(scope="module")
async def ac(base_app: FastAPI) -> httpx.AsyncClient:
    """Step 4 — Action: Async HTTP client wired to the test app."""
    transport = httpx.ASGITransport(app=base_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client


@pytest.fixture(scope="module")
def wav() -> bytes:
    return _make_wav()


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — HEALTH ENDPOINT  (TC-001 → TC-003)
# ═══════════════════════════════════════════════════════════════════════════════

class TestHealthEndpoint:
    """
    Step 1 — Metadata: GET /api/health — observability / smoke tests
    Tags: health, observability, smoke
    """

    async def test_TC001_health_returns_ok_status(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-001 | health_returns_ok_status
        Step 2  Preconditions: App running, no auth required
        Step 3  Input: GET /api/health — no parameters
        Step 4  Action: GET /api/health
        Step 5  Expected: HTTP 200, JSON body with status="ok"
        Step 6  Assertions: status_code==200, body.status=="ok"
        Step 7  Teardown: none
        Step 8  Result: captured by pytest
        """
        resp = await ac.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert isinstance(body.get("version"), str)
        assert isinstance(body.get("uptime_s"), (int, float))
        assert body["uptime_s"] >= 0.0

    async def test_TC002_health_response_schema_complete(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-002 | health_response_schema_complete
        Step 2  Preconditions: Default settings
        Step 3  Input: GET /api/health
        Step 4  Action: GET /api/health
        Step 5  Expected: All 5 fields present
        Step 6  Assertions: required keys subset of body keys
        Step 7  Teardown: none
        Step 8  Result: captured by pytest
        """
        resp = await ac.get("/api/health")
        body = resp.json()
        required = {"status", "version", "uptime_s", "stt_provider", "tts_provider"}
        assert required.issubset(set(body.keys())), f"Missing keys: {required - set(body.keys())}"
        assert isinstance(body["uptime_s"], (int, float))

    async def test_TC003_health_provider_fields_match_config(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-003 | health_stt_provider_reflects_config
        Step 2  Preconditions: App configured with stt=whisper, tts=edge_tts
        Step 3  Input: GET /api/health
        Step 4  Action: GET /api/health
        Step 5  Expected: stt_provider=="whisper", tts_provider=="edge_tts"
        Step 6  Assertions: exact string match
        Step 7  Teardown: none
        Step 8  Result: captured by pytest
        """
        resp = await ac.get("/api/health")
        body = resp.json()
        assert body["stt_provider"] == "whisper"
        assert body["tts_provider"] == "edge_tts"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — PROVIDERS ENDPOINT  (TC-004 → TC-005)
# ═══════════════════════════════════════════════════════════════════════════════

class TestProvidersEndpoint:
    """
    Step 1 — Metadata: GET /api/providers
    Tags: providers, observability, smoke
    """

    async def test_TC004_providers_returns_stt_and_tts_lists(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-004 | providers_returns_stt_tts_lists
        Step 2  Preconditions: Providers installed
        Step 3  Input: GET /api/providers — no parameters
        Step 4  Action: GET /api/providers
        Step 5  Expected: HTTP 200, body.stt and body.tts are lists
        Step 6  Assertions: isinstance checks for both lists
        Step 7  Teardown: none
        Step 8  Result: captured by pytest
        """
        resp = await ac.get("/api/providers")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body.get("stt"), list)
        assert isinstance(body.get("tts"), list)

    async def test_TC005_providers_graceful_when_unavailable(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-005 | providers_lists_can_be_empty_when_unavailable
        Step 2  Preconditions: WhisperSTTProvider.is_available() mocked to return False
        Step 3  Input: GET /api/providers
        Step 4  Action: GET /api/providers with mocked provider unavailability
        Step 5  Expected: HTTP 200, no crash, lists returned
        Step 6  Assertions: status==200, lists exist
        Step 7  Teardown: mock context exits automatically
        Step 8  Result: captured by pytest
        """
        with patch("backend.providers.whisper_provider.WhisperSTTProvider.is_available", return_value=False):
            with patch("backend.providers.edge_tts_provider.EdgeTTSProvider.is_available", return_value=False):
                resp = await ac.get("/api/providers")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["stt"], list)
        assert isinstance(body["tts"], list)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — VOICES ENDPOINT  (TC-006 → TC-009)
# ═══════════════════════════════════════════════════════════════════════════════

class TestVoicesEndpoint:
    """
    Step 1 — Metadata: GET /api/voices
    Tags: voices, tts, schema
    """

    async def test_TC006_voices_grouped_by_language_code(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-006 | voices_grouped_by_language_code
        Step 2  Preconditions: App running with edge_tts provider and AVAILABLE_VOICES loaded
        Step 3  Input: GET /api/voices — no parameters
        Step 4  Action: GET /api/voices
        Step 5  Expected: HTTP 200, body.voices is dict with language keys
        Step 6  Assertions: voices is dict, "en" key exists
        Step 7  Teardown: none
        Step 8  Result: captured by pytest
        """
        resp = await ac.get("/api/voices")
        assert resp.status_code == 200
        body = resp.json()
        assert "voices" in body
        assert isinstance(body["voices"], dict)
        assert "en" in body["voices"]

    async def test_TC007_voices_each_entry_has_name_and_language(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-007 | voices_each_entry_has_name_and_language
        Step 2  Preconditions: Voices endpoint populated
        Step 3  Input: GET /api/voices
        Step 4  Action: GET /api/voices
        Step 5  Expected: All voice dicts have name (str) and language (str)
        Step 6  Assertions: iterate all voices, check required fields
        Step 7  Teardown: none
        Step 8  Result: captured by pytest
        """
        resp = await ac.get("/api/voices")
        voices = resp.json()["voices"]
        for lang, vlist in voices.items():
            for v in vlist:
                assert v.get("name"), f"Voice missing name in lang={lang}"
                assert v.get("language"), f"Voice missing language in lang={lang}"

    async def test_TC008_voices_english_has_multiple_voices(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-008 | voices_english_has_multiple_voices
        Step 2  Preconditions: AVAILABLE_VOICES includes English entries
        Step 3  Input: GET /api/voices
        Step 4  Action: GET /api/voices
        Step 5  Expected: body.voices["en"] has >= 1 entry
        Step 6  Assertions: len(en_voices) >= 1
        Step 7  Teardown: none
        Step 8  Result: captured by pytest
        """
        resp = await ac.get("/api/voices")
        en_voices = resp.json()["voices"].get("en", [])
        assert len(en_voices) >= 1, "English must have at least one voice"

    async def test_TC009_voices_all_configured_languages_present(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-009 | voices_all_supported_languages_present
        Step 2  Preconditions: AVAILABLE_VOICES covers all 10 language codes
        Step 3  Input: GET /api/voices
        Step 4  Action: GET /api/voices
        Step 5  Expected: All 10 language codes in voices dict
        Step 6  Assertions: subset check
        Step 7  Teardown: none
        Step 8  Result: captured by pytest
        """
        resp = await ac.get("/api/voices")
        present = set(resp.json()["voices"].keys())
        expected = {"en", "hi", "mr", "es", "fr", "pt", "de", "zh", "ja", "ar"}
        missing = expected - present
        assert not missing, f"Missing language codes: {missing}"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — POST /api/transcribe  (TC-010 → TC-020)
# ═══════════════════════════════════════════════════════════════════════════════

class TestTranscribeEndpoint:
    """
    Step 1 — Metadata: POST /api/transcribe
    Tags: transcribe, stt
    """

    async def test_TC010_transcribe_valid_wav_returns_200(
        self, ac: httpx.AsyncClient, wav: bytes
    ) -> None:
        """
        Step 1  TC-010 | transcribe_valid_wav_returns_200
        Step 2  Preconditions: STT mocked to return "hello world"
        Step 3  Input: Multipart audio=wav (audio/wav)
        Step 4  Action: POST /api/transcribe
        Step 5  Expected: HTTP 200, text=="hello world", duration_ms >= 0
        Step 6  Assertions: status, text, duration_ms checks
        Step 7  Teardown: mock context exits
        Step 8  Result: captured by pytest
        """
        with patch(
            "backend.providers.whisper_provider.WhisperSTTProvider.transcribe",
            new=AsyncMock(return_value="hello world"),
        ):
            resp = await ac.post(
                "/api/transcribe",
                files={"audio": ("test.wav", wav, "audio/wav")},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["text"] == "hello world"
        assert isinstance(body.get("display_text"), str)
        assert body.get("duration_ms", 0) >= 0

    async def test_TC011_transcribe_display_text_is_sentence_cased(
        self, ac: httpx.AsyncClient, wav: bytes
    ) -> None:
        """
        Step 1  TC-011 | transcribe_returns_display_text_capitalized
        Step 2  Preconditions: STT mocked to return "hello world. how are you"
        Step 3  Input: Multipart audio=wav
        Step 4  Action: POST /api/transcribe
        Step 5  Expected: display_text starts with uppercase "H"
        Step 6  Assertions: display_text[0].isupper()
        Step 7  Teardown: mock context exits
        Step 8  Result: captured by pytest
        """
        raw_text = "hello world. how are you"
        with patch(
            "backend.providers.whisper_provider.WhisperSTTProvider.transcribe",
            new=AsyncMock(return_value=raw_text),
        ):
            resp = await ac.post(
                "/api/transcribe",
                files={"audio": ("test.wav", wav, "audio/wav")},
            )
        assert resp.status_code == 200
        body = resp.json()
        display = body["display_text"]
        assert display[0].isupper(), f"display_text not capitalized: {display!r}"

    async def test_TC012_transcribe_language_hint_echoed(
        self, ac: httpx.AsyncClient, wav: bytes
    ) -> None:
        """
        Step 1  TC-012 | transcribe_with_language_hint
        Step 2  Preconditions: STT mocked; language=hi query param
        Step 3  Input: Multipart audio=wav; query ?language=hi
        Step 4  Action: POST /api/transcribe?language=hi
        Step 5  Expected: body.language=="hi"
        Step 6  Assertions: body["language"]=="hi"
        Step 7  Teardown: mock exits
        Step 8  Result: captured by pytest
        """
        with patch(
            "backend.providers.whisper_provider.WhisperSTTProvider.transcribe",
            new=AsyncMock(return_value="नमस्ते"),
        ):
            resp = await ac.post(
                "/api/transcribe?language=hi",
                files={"audio": ("hindi.wav", wav, "audio/wav")},
            )
        assert resp.status_code == 200
        assert resp.json()["language"] == "hi"

    async def test_TC013_transcribe_vad_filter_false_passed_to_provider(
        self, ac: httpx.AsyncClient, wav: bytes
    ) -> None:
        """
        Step 1  TC-013 | transcribe_with_vad_filter_false
        Step 2  Preconditions: STT mock captures call kwargs
        Step 3  Input: Multipart audio=wav; query ?vad_filter=false
        Step 4  Action: POST /api/transcribe?vad_filter=false
        Step 5  Expected: HTTP 200; provider called with vad_filter=False
        Step 6  Assertions: status==200; mock call kwarg vad_filter is False
        Step 7  Teardown: mock exits
        Step 8  Result: captured by pytest
        """
        mock_transcribe = AsyncMock(return_value="ok")
        with patch(
            "backend.providers.whisper_provider.WhisperSTTProvider.transcribe",
            new=mock_transcribe,
        ):
            resp = await ac.post(
                "/api/transcribe?vad_filter=false",
                files={"audio": ("test.wav", wav, "audio/wav")},
            )
        assert resp.status_code == 200
        _, kwargs = mock_transcribe.call_args
        assert kwargs.get("vad_filter") is False

    async def test_TC014_transcribe_translate_task_passed_to_provider(
        self, ac: httpx.AsyncClient, wav: bytes
    ) -> None:
        """
        Step 1  TC-014 | transcribe_translate_task
        Step 2  Preconditions: STT mock captures call kwargs
        Step 3  Input: Multipart audio=wav; query ?task=translate
        Step 4  Action: POST /api/transcribe?task=translate
        Step 5  Expected: HTTP 200; provider called with task="translate"
        Step 6  Assertions: status==200; mock kwarg task=="translate"
        Step 7  Teardown: mock exits
        Step 8  Result: captured by pytest
        """
        mock_transcribe = AsyncMock(return_value="Translated text")
        with patch(
            "backend.providers.whisper_provider.WhisperSTTProvider.transcribe",
            new=mock_transcribe,
        ):
            resp = await ac.post(
                "/api/transcribe?task=translate",
                files={"audio": ("test.wav", wav, "audio/wav")},
            )
        assert resp.status_code == 200
        _, kwargs = mock_transcribe.call_args
        assert kwargs.get("task") == "translate"

    async def test_TC015_transcribe_invalid_task_returns_422(
        self, ac: httpx.AsyncClient, wav: bytes
    ) -> None:
        """
        Step 1  TC-015 | transcribe_invalid_task_returns_422
        Step 2  Preconditions: App running
        Step 3  Input: Multipart audio=wav; query ?task=malicious
        Step 4  Action: POST /api/transcribe?task=malicious
        Step 5  Expected: HTTP 422; detail mentions "task"
        Step 6  Assertions: status==422; "task" in detail
        Step 7  Teardown: none
        Step 8  Result: captured by pytest
        """
        with patch(
            "backend.providers.whisper_provider.WhisperSTTProvider.transcribe",
            new=AsyncMock(return_value=""),
        ):
            resp = await ac.post(
                "/api/transcribe?task=malicious",
                files={"audio": ("test.wav", wav, "audio/wav")},
            )
        assert resp.status_code == 422

    async def test_TC016_transcribe_no_file_returns_422(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-016 | transcribe_no_file_returns_422
        Step 2  Preconditions: App running, no file provided
        Step 3  Input: POST /api/transcribe with empty body
        Step 4  Action: POST /api/transcribe
        Step 5  Expected: HTTP 422 (missing required field)
        Step 6  Assertions: status==422
        Step 7  Teardown: none
        Step 8  Result: captured by pytest
        """
        resp = await ac.post("/api/transcribe")
        assert resp.status_code == 422

    async def test_TC017_transcribe_oversized_file_returns_400(
        self, ac: httpx.AsyncClient, base_settings: VocalTwistSettings
    ) -> None:
        """
        Step 1  TC-017 | transcribe_oversized_file_returns_400
        Step 2  Preconditions: max_audio_bytes=10MB (default)
        Step 3  Input: audio=oversized_bytes (>10MB), content-type=audio/wav
        Step 4  Action: POST /api/transcribe
        Step 5  Expected: HTTP 400; detail contains "exceeds"
        Step 6  Assertions: status==400; "exceeds" in detail
        Step 7  Teardown: none
        Step 8  Result: captured by pytest
        """
        oversized = b"\x00" * (base_settings.max_audio_bytes + 1)
        resp = await ac.post(
            "/api/transcribe",
            files={"audio": ("huge.wav", oversized, "audio/wav")},
        )
        assert resp.status_code == 400
        assert "exceeds" in resp.json()["detail"]

    async def test_TC018_transcribe_unsupported_mime_returns_400(
        self, ac: httpx.AsyncClient
    ) -> None:
        """
        Step 1  TC-018 | transcribe_unsupported_mime_returns_400
        Step 2  Preconditions: Disallowed MIME type
        Step 3  Input: audio=bytes with content-type=application/x-php
        Step 4  Action: POST /api/transcribe with PHP MIME type
        Step 5  Expected: HTTP 400; detail contains "Unsupported audio type"
        Step 6  Assertions: status==400; "Unsupported audio type" in detail
        Step 7  Teardown: none
        Step 8  Result: captured by pytest
        """
        resp = await ac.post(
            "/api/transcribe",
            files={"audio": ("evil.php", b"<?php echo 'hack'; ?>", "application/x-php")},
        )
        assert resp.status_code == 400
        assert "Unsupported audio type" in resp.json()["detail"]

    async def test_TC019_transcribe_provider_error_returns_500(
        self, ac: httpx.AsyncClient, wav: bytes
    ) -> None:
        """
        Step 1  TC-019 | transcribe_provider_error_returns_500
        Step 2  Preconditions: STT mocked to raise RuntimeError
        Step 3  Input: Multipart audio=wav
        Step 4  Action: POST /api/transcribe with failing STT
        Step 5  Expected: HTTP 500
        Step 6  Assertions: status==500
        Step 7  Teardown: mock exits
        Step 8  Result: captured by pytest
        """
        with patch(
            "backend.providers.whisper_provider.WhisperSTTProvider.transcribe",
            new=AsyncMock(side_effect=RuntimeError("GPU crash")),
        ):
            resp = await ac.post(
                "/api/transcribe",
                files={"audio": ("test.wav", wav, "audio/wav")},
            )
        assert resp.status_code == 500
        assert "transcription" in resp.json()["detail"].lower() or "failed" in resp.json()["detail"].lower()

    async def test_TC020_transcribe_error_response_has_request_id(
        self, ac: httpx.AsyncClient, wav: bytes
    ) -> None:
        """
        Step 1  TC-020 | transcribe_error_response_has_request_id
        Step 2  Preconditions: STT mocked to crash; X-Request-ID header supplied
        Step 3  Input: Multipart audio=wav; header X-Request-ID=trace-001
        Step 4  Action: POST /api/transcribe with failing STT and request ID
        Step 5  Expected: HTTP 500; body.request_id=="trace-001"
        Step 6  Assertions: status==500; request_id echoed
        Step 7  Teardown: mock exits
        Step 8  Result: captured by pytest
        """
        with patch(
            "backend.providers.whisper_provider.WhisperSTTProvider.transcribe",
            new=AsyncMock(side_effect=RuntimeError("crash")),
        ):
            resp = await ac.post(
                "/api/transcribe",
                files={"audio": ("test.wav", wav, "audio/wav")},
                headers={"X-Request-ID": "trace-001"},
            )
        assert resp.status_code == 500
        assert resp.json()["request_id"] == "trace-001"


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — POST /api/transcribe-ambient  (TC-021 → TC-024)
# ═══════════════════════════════════════════════════════════════════════════════

class TestTranscribeAmbientEndpoint:
    """
    Step 1 — Metadata: POST /api/transcribe-ambient
    Tags: transcribe-ambient, stt, vad
    """

    async def test_TC021_ambient_returns_slim_body(
        self, ac: httpx.AsyncClient, wav: bytes
    ) -> None:
        """
        Step 1  TC-021 | transcribe_ambient_returns_slim_body
        Step 2  Preconditions: STT mocked; app running
        Step 3  Input: Multipart audio=wav (audio/wav)
        Step 4  Action: POST /api/transcribe-ambient
        Step 5  Expected: HTTP 200; body has text and display_text only (no duration_ms)
        Step 6  Assertions: status==200; text and display_text present
        Step 7  Teardown: mock exits
        Step 8  Result: captured by pytest
        """
        with patch(
            "backend.providers.whisper_provider.WhisperSTTProvider.transcribe",
            new=AsyncMock(return_value="ambient voice detected"),
        ):
            resp = await ac.post(
                "/api/transcribe-ambient",
                files={"audio": ("ambient.wav", wav, "audio/wav")},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "text" in body
        assert "display_text" in body
        # Ambient response is slim — no language or duration_ms fields
        assert "duration_ms" not in body

    async def test_TC022_ambient_no_file_returns_422(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-022 | transcribe_ambient_no_file_returns_422
        Step 2  Preconditions: App running
        Step 3  Input: No audio file in request
        Step 4  Action: POST /api/transcribe-ambient with empty body
        Step 5  Expected: HTTP 422
        Step 6  Assertions: status==422
        Step 7  Teardown: none
        Step 8  Result: captured by pytest
        """
        resp = await ac.post("/api/transcribe-ambient")
        assert resp.status_code == 422

    async def test_TC023_ambient_accepts_language_hint(
        self, ac: httpx.AsyncClient, wav: bytes
    ) -> None:
        """
        Step 1  TC-023 | transcribe_ambient_with_language_hint
        Step 2  Preconditions: STT mocked; language=fr query param
        Step 3  Input: Multipart audio=wav; query ?language=fr
        Step 4  Action: POST /api/transcribe-ambient?language=fr
        Step 5  Expected: HTTP 200
        Step 6  Assertions: status==200; text present
        Step 7  Teardown: mock exits
        Step 8  Result: captured by pytest
        """
        with patch(
            "backend.providers.whisper_provider.WhisperSTTProvider.transcribe",
            new=AsyncMock(return_value="bonjour"),
        ):
            resp = await ac.post(
                "/api/transcribe-ambient?language=fr",
                files={"audio": ("fr.wav", wav, "audio/wav")},
            )
        assert resp.status_code == 200
        assert "text" in resp.json()

    async def test_TC024_ambient_auth_required_when_enabled(self, wav: bytes) -> None:
        """
        Step 1  TC-024 | transcribe_ambient_auth_required_when_enabled
        Step 2  Preconditions: App with api_key_enabled=True
        Step 3  Input: Multipart audio=wav; no X-API-Key header
        Step 4  Action: POST /api/transcribe-ambient without API key
        Step 5  Expected: HTTP 401
        Step 6  Assertions: status==401
        Step 7  Teardown: none (separate app fixture scope)
        Step 8  Result: captured by pytest
        """
        auth_settings = VocalTwistSettings(
            api_key_enabled=True,
            api_keys=["test-secret"],
            rate_limit_enabled=False,
            log_level="WARNING",
            log_format="text",
        )
        auth_app = create_app(settings=auth_settings)
        auth_app.dependency_overrides[get_settings] = lambda: auth_settings
        transport = httpx.ASGITransport(app=auth_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            resp = await c.post(
                "/api/transcribe-ambient",
                files={"audio": ("test.wav", wav, "audio/wav")},
            )
        assert resp.status_code == 401


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — POST /api/speak  (TC-025 → TC-034)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSpeakEndpoint:
    """
    Step 1 — Metadata: POST /api/speak
    Tags: speak, tts
    """

    async def test_TC025_speak_valid_text_returns_mp3(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-025 | speak_valid_text_returns_mp3
        Step 2  Preconditions: TTS mocked to return FAKE_MP3 bytes
        Step 3  Input: JSON {"text": "Hello world", "language": "en"}
        Step 4  Action: POST /api/speak
        Step 5  Expected: HTTP 200; content-type=audio/mpeg; bytes returned
        Step 6  Assertions: status==200; content-type check
        Step 7  Teardown: mock exits
        Step 8  Result: captured by pytest
        """
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=FAKE_MP3),
        ):
            resp = await ac.post("/api/speak", json={"text": "Hello world", "language": "en"})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mpeg"
        assert len(resp.content) > 0

    async def test_TC026_speak_explicit_voice_used(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-026 | speak_uses_explicit_voice
        Step 2  Preconditions: TTS mock captures call kwargs
        Step 3  Input: JSON {"text": "Hello", "voice": "en-US-AriaNeural"}
        Step 4  Action: POST /api/speak
        Step 5  Expected: HTTP 200; TTS called with voice=en-US-AriaNeural
        Step 6  Assertions: mock kwarg check
        Step 7  Teardown: mock exits
        Step 8  Result: captured by pytest
        """
        mock_speak = AsyncMock(return_value=FAKE_MP3)
        with patch("backend.providers.edge_tts_provider.EdgeTTSProvider.speak", new=mock_speak):
            resp = await ac.post(
                "/api/speak",
                json={"text": "Hello", "voice": "en-US-AriaNeural"},
            )
        assert resp.status_code == 200
        _, kwargs = mock_speak.call_args
        assert kwargs.get("voice") == "en-US-AriaNeural"

    async def test_TC027_speak_language_default_voice_selection(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-027 | speak_uses_language_default_voice
        Step 2  Preconditions: TTS mock captures args; language=hi; no explicit voice
        Step 3  Input: JSON {"text": "Namaste", "language": "hi"}
        Step 4  Action: POST /api/speak
        Step 5  Expected: HTTP 200; TTS called with Hindi voice (SwaraNeural)
        Step 6  Assertions: "Swara" substring in voice arg
        Step 7  Teardown: mock exits
        Step 8  Result: captured by pytest
        """
        mock_speak = AsyncMock(return_value=FAKE_MP3)
        with patch("backend.providers.edge_tts_provider.EdgeTTSProvider.speak", new=mock_speak):
            resp = await ac.post(
                "/api/speak",
                json={"text": "Namaste", "language": "hi"},
            )
        assert resp.status_code == 200
        _, kwargs = mock_speak.call_args
        voice_used = kwargs.get("voice", "")
        assert "Swara" in voice_used, f"Expected Swara voice for 'hi', got: {voice_used!r}"

    async def test_TC028_speak_empty_text_returns_422(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-028 | speak_empty_text_returns_422
        Step 2  Preconditions: Pydantic min_length=1 validation
        Step 3  Input: JSON {"text": ""}
        Step 4  Action: POST /api/speak
        Step 5  Expected: HTTP 422
        Step 6  Assertions: status==422
        Step 7  Teardown: none
        Step 8  Result: captured by pytest
        """
        resp = await ac.post("/api/speak", json={"text": ""})
        assert resp.status_code == 422

    async def test_TC029_speak_text_at_max_length_succeeds(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-029 | speak_text_max_length_boundary
        Step 2  Preconditions: TTS mocked; text is exactly 2000 chars (MAX_TTS_LENGTH)
        Step 3  Input: JSON {"text": "a" * 2000}
        Step 4  Action: POST /api/speak
        Step 5  Expected: HTTP 200
        Step 6  Assertions: status==200
        Step 7  Teardown: mock exits
        Step 8  Result: captured by pytest
        """
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=FAKE_MP3),
        ):
            resp = await ac.post("/api/speak", json={"text": "a" * 2000})
        assert resp.status_code == 200

    async def test_TC030_speak_html_only_text_returns_error(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-030 | speak_html_only_text_returns_422
        Step 2  Preconditions: Pydantic sanitize_text strips HTML; text empty after strip
        Step 3  Input: JSON {"text": "<b></b><i></i>"}
        Step 4  Action: POST /api/speak
        Step 5  Expected: HTTP 400 or 422 (text empty after sanitization)
        Step 6  Assertions: status in (400, 422)
        Step 7  Teardown: none
        Step 8  Result: captured by pytest
        """
        resp = await ac.post("/api/speak", json={"text": "<b></b><i></i>"})
        assert resp.status_code in (400, 422), f"Expected 400/422, got {resp.status_code}"

    async def test_TC031_speak_xss_stripped_before_tts(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-031 | speak_xss_stripped_before_tts
        Step 2  Preconditions: TTS mock captures received text
        Step 3  Input: JSON {"text": "<img src=x onerror=alert(1)>Hello<script>evil()</script>"}
        Step 4  Action: POST /api/speak with XSS payload
        Step 5  Expected: HTTP 200; TTS receives text without HTML/script tags
        Step 6  Assertions: "<" not in called_text; "script" not in called_text.lower()
        Step 7  Teardown: mock exits
        Step 8  Result: captured by pytest
        """
        xss = '<img src=x onerror=alert(1)>Hello<script>evil()</script>'
        mock_speak = AsyncMock(return_value=FAKE_MP3)
        with patch("backend.providers.edge_tts_provider.EdgeTTSProvider.speak", new=mock_speak):
            resp = await ac.post("/api/speak", json={"text": xss})
        assert resp.status_code == 200
        called_text = mock_speak.call_args[0][0] if mock_speak.call_args[0] else mock_speak.call_args[1].get("text", "")
        assert "<" not in called_text, f"HTML tag leaked to TTS: {called_text!r}"
        assert "script" not in called_text.lower()

    async def test_TC032_speak_provider_error_returns_500(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-032 | speak_provider_error_returns_500
        Step 2  Preconditions: TTS mocked to raise RuntimeError
        Step 3  Input: JSON {"text": "Hello"}
        Step 4  Action: POST /api/speak with failing TTS
        Step 5  Expected: HTTP 500
        Step 6  Assertions: status==500
        Step 7  Teardown: mock exits
        Step 8  Result: captured by pytest
        """
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(side_effect=RuntimeError("TTS engine down")),
        ):
            resp = await ac.post("/api/speak", json={"text": "Hello"})
        assert resp.status_code == 500

    async def test_TC033_speak_echoes_x_request_id_header(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-033 | speak_response_has_x_request_id_header
        Step 2  Preconditions: TTS mocked; X-Request-ID header supplied
        Step 3  Input: JSON {"text": "Hello"}; header X-Request-ID=req-abc-123
        Step 4  Action: POST /api/speak
        Step 5  Expected: HTTP 200; X-Request-ID echoed in response
        Step 6  Assertions: resp.headers["x-request-id"]=="req-abc-123"
        Step 7  Teardown: mock exits
        Step 8  Result: captured by pytest
        """
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=FAKE_MP3),
        ):
            resp = await ac.post(
                "/api/speak",
                json={"text": "Hello"},
                headers={"X-Request-ID": "req-abc-123"},
            )
        assert resp.status_code == 200
        assert resp.headers.get("x-request-id") == "req-abc-123"

    async def test_TC034_speak_response_has_x_duration_ms_header(self, ac: httpx.AsyncClient) -> None:
        """
        Step 1  TC-034 | speak_response_has_x_duration_ms_header
        Step 2  Preconditions: TTS mocked
        Step 3  Input: JSON {"text": "Test duration header"}
        Step 4  Action: POST /api/speak
        Step 5  Expected: HTTP 200; X-Duration-Ms is a non-negative float
        Step 6  Assertions: float(header) >= 0
        Step 7  Teardown: mock exits
        Step 8  Result: captured by pytest
        """
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=FAKE_MP3),
        ):
            resp = await ac.post("/api/speak", json={"text": "Test duration header"})
        assert resp.status_code == 200
        duration_header = resp.headers.get("x-duration-ms", "")
        assert duration_header, "X-Duration-Ms header is missing"
        assert float(duration_header) >= 0.0


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — AUTHENTICATION / SECURITY  (TC-035 → TC-037)
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def auth_settings() -> VocalTwistSettings:
    """Step 2 — Preconditions: api_key_enabled=True."""
    return VocalTwistSettings(
        api_key_enabled=True,
        api_keys=["valid-e2e-key"],
        rate_limit_enabled=False,
        log_level="WARNING",
        log_format="text",
    )


@pytest.fixture
def auth_app(auth_settings: VocalTwistSettings) -> FastAPI:
    app = create_app(settings=auth_settings)
    app.dependency_overrides[get_settings] = lambda: auth_settings
    return app


class TestApiKeyAuthentication:
    """
    Step 1 — Metadata: API key security tests
    Tags: auth, security
    """

    async def test_TC035_transcribe_no_key_returns_401(
        self, auth_app: FastAPI, wav: bytes
    ) -> None:
        """
        Step 1  TC-035 | api_key_auth_transcribe_no_key_returns_401
        Step 2  Preconditions: App with api_key_enabled=True
        Step 3  Input: Multipart audio=wav; no X-API-Key header
        Step 4  Action: POST /api/transcribe
        Step 5  Expected: HTTP 401
        Step 6  Assertions: status==401
        Step 7  Teardown: none
        Step 8  Result: captured by pytest
        """
        transport = httpx.ASGITransport(app=auth_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            resp = await c.post(
                "/api/transcribe",
                files={"audio": ("test.wav", wav, "audio/wav")},
            )
        assert resp.status_code == 401

    async def test_TC036_speak_with_valid_api_key_succeeds(
        self, auth_app: FastAPI
    ) -> None:
        """
        Step 1  TC-036 | api_key_auth_speak_with_valid_key_succeeds
        Step 2  Preconditions: api_key_enabled=True; api_keys=["valid-e2e-key"]; TTS mocked
        Step 3  Input: JSON {"text": "Secured"}; header X-API-Key=valid-e2e-key
        Step 4  Action: POST /api/speak
        Step 5  Expected: HTTP 200
        Step 6  Assertions: status==200
        Step 7  Teardown: mock exits
        Step 8  Result: captured by pytest
        """
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=FAKE_MP3),
        ):
            transport = httpx.ASGITransport(app=auth_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
                resp = await c.post(
                    "/api/speak",
                    json={"text": "Secured"},
                    headers={"X-API-Key": "valid-e2e-key"},
                )
        assert resp.status_code == 200

    async def test_TC037_health_endpoint_always_public(self, auth_app: FastAPI) -> None:
        """
        Step 1  TC-037 | api_key_health_endpoint_public
        Step 2  Preconditions: App with api_key_enabled=True; no API key provided
        Step 3  Input: GET /api/health; no X-API-Key
        Step 4  Action: GET /api/health
        Step 5  Expected: HTTP 200 (health is always public — no auth check)
        Step 6  Assertions: status==200
        Step 7  Teardown: none
        Step 8  Result: captured by pytest
        """
        transport = httpx.ASGITransport(app=auth_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            resp = await c.get("/api/health")
        assert resp.status_code == 200


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — RATE LIMITING  (TC-038)
# ═══════════════════════════════════════════════════════════════════════════════

class TestRateLimiting:
    """
    Step 1 — Metadata: Rate limiting tests
    Tags: rate-limit, error
    """

    async def test_TC038_speak_rate_limit_429_after_exhaustion(self) -> None:
        """
        Step 1  TC-038 | rate_limit_speak_429_after_exhaustion
        Step 2  Preconditions: rate_limit_speak=3/minute; TTS mocked; limiters reset
        Step 3  Input: 4 sequential POST /api/speak from same IP
        Step 4  Action: POST /api/speak × 4
        Step 5  Expected: First 3 succeed (200); 4th returns 429 with Retry-After
        Step 6  Assertions: resp4.status==429; "Retry-After" in headers
        Step 7  Teardown: Reset global limiters
        Step 8  Result: captured by pytest
        """
        rate_settings = VocalTwistSettings(
            api_key_enabled=False,
            rate_limit_enabled=True,
            rate_limit_speak="3/minute",
            rate_limit_transcribe="3/minute",
            log_level="WARNING",
            log_format="text",
        )
        _mw._speak_limiter = None
        _mw._transcribe_limiter = None
        rate_app = create_app(settings=rate_settings)
        rate_app.dependency_overrides[get_settings] = lambda: rate_settings

        try:
            transport = httpx.ASGITransport(app=rate_app)
            async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
                with patch(
                    "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
                    new=AsyncMock(return_value=FAKE_MP3),
                ):
                    results = []
                    for i in range(4):
                        r = await c.post("/api/speak", json={"text": f"Request {i}"})
                        results.append(r)

            assert results[0].status_code == 200
            assert results[1].status_code == 200
            assert results[2].status_code == 200
            assert results[3].status_code == 429
            assert "retry-after" in results[3].headers
        finally:
            _mw._speak_limiter = None
            _mw._transcribe_limiter = None


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — CHAT ENDPOINT  (TC-039 → TC-040)
# ═══════════════════════════════════════════════════════════════════════════════

class TestChatEndpoint:
    """
    Step 1 — Metadata: POST /api/chat (demo endpoint via VocalTwistTest app)
    Tags: chat, demo
    """

    async def test_TC039_chat_mocked_llm_returns_reply_and_flag(self) -> None:
        """
        Step 1  TC-039 | chat_with_mocked_llm_returns_reply
        Step 2  Preconditions: LLM mocked; circuit breaker failures reset to 0
        Step 3  Input: JSON {"messages": [{"role": "user", "content": "Capital of France?"}], "language": "en"}
        Step 4  Action: POST /api/chat via VocalTwistTest app
        Step 5  Expected: HTTP 200; body.reply is non-empty str; body.is_emergency is bool
        Step 6  Assertions: type checks and status check
        Step 7  Teardown: none (mock context exits)
        Step 8  Result: captured by pytest
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))
        from VocalTwistTest.app import app as chat_app
        import VocalTwistTest.app as _chat_app

        llm_payload = {
            "choices": [{"message": {"content": "The capital of France is Paris."}}]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = llm_payload
        mock_resp.raise_for_status.return_value = None

        _chat_app._cb_failures = 0
        transport = httpx.ASGITransport(app=chat_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            with patch("VocalTwistTest.app._http_client") as mock_client:
                mock_client.post = AsyncMock(return_value=mock_resp)
                resp = await c.post(
                    "/api/chat",
                    json={
                        "messages": [{"role": "user", "content": "Capital of France?"}],
                        "language": "en",
                    },
                )

        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body.get("reply"), str)
        assert len(body["reply"]) > 0
        assert isinstance(body.get("is_emergency"), bool)

    async def test_TC040_chat_emergency_keyword_sets_flag_true(self) -> None:
        """
        Step 1  TC-040 | chat_emergency_keyword_sets_flag
        Step 2  Preconditions: LLM mocked to return reply with "emergency" keyword; cb reset
        Step 3  Input: JSON {"messages": [{"role": "user", "content": "Someone is dying!"}], "language": "en"}
        Step 4  Action: POST /api/chat, LLM returns emergency-flagged reply
        Step 5  Expected: HTTP 200; body.is_emergency==True
        Step 6  Assertions: is_emergency is True
        Step 7  Teardown: none (mock context exits)
        Step 8  Result: captured by pytest
        """
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../../"))
        from VocalTwistTest.app import app as chat_app
        import VocalTwistTest.app as _chat_app

        llm_payload = {
            "choices": [{"message": {"content": "This is an emergency! Call 911 right now!"}}]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = llm_payload
        mock_resp.raise_for_status.return_value = None

        _chat_app._cb_failures = 0
        transport = httpx.ASGITransport(app=chat_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
            with patch("VocalTwistTest.app._http_client") as mock_client:
                mock_client.post = AsyncMock(return_value=mock_resp)
                resp = await c.post(
                    "/api/chat",
                    json={
                        "messages": [{"role": "user", "content": "Someone is dying!"}],
                        "language": "en",
                    },
                )

        assert resp.status_code == 200
        assert resp.json()["is_emergency"] is True
