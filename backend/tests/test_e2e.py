"""End-to-end tests for the VocalTwist REST API."""
from __future__ import annotations
import asyncio, io, wave
from unittest.mock import AsyncMock, patch
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
import backend.middleware as _mw
from backend.config import VocalTwistSettings, get_settings
from backend.middleware import create_app

def _make_wav(duration_secs: float = 0.5, sample_rate: int = 16_000) -> bytes:
    n = int(sample_rate * duration_secs)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00" * n * 2)
    return buf.getvalue()

FAKE_MP3 = b"\xff\xfb\x90\x00" + b"\x00" * 128

@pytest.fixture(scope="module")
def e2e_settings() -> VocalTwistSettings:
    return VocalTwistSettings(api_key_enabled=False, rate_limit_enabled=False, log_level="WARNING", log_format="text")

@pytest.fixture(scope="module")
def e2e_app(e2e_settings: VocalTwistSettings) -> FastAPI:
    app = create_app(settings=e2e_settings)
    app.dependency_overrides[get_settings] = lambda: e2e_settings
    return app

@pytest_asyncio.fixture(scope="module")
async def ac(e2e_app: FastAPI) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=e2e_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client

@pytest.fixture(scope="module")
def wav() -> bytes:
    return _make_wav()

class TestServiceDiscovery:
    async def test_health_returns_ok(self, ac: httpx.AsyncClient) -> None:
        resp = await ac.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert "version" in body
        assert body["uptime_s"] >= 0.0

    async def test_providers_returns_lists(self, ac: httpx.AsyncClient) -> None:
        resp = await ac.get("/api/providers")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body.get("stt"), list)
        assert isinstance(body.get("tts"), list)

    async def test_voices_grouped_by_language(self, ac: httpx.AsyncClient) -> None:
        resp = await ac.get("/api/voices")
        assert resp.status_code == 200
        body = resp.json()
        assert "voices" in body
        assert isinstance(body["voices"], dict)
        assert "en" in body["voices"]
        for v in body["voices"]["en"]:
            assert "name" in v and "language" in v

class TestVoiceRoundTrip:
    async def test_transcribe_then_speak(self, ac: httpx.AsyncClient, wav: bytes) -> None:
        transcript = "Good morning, how can I help you?"
        with (
            patch("backend.providers.whisper_provider.WhisperSTTProvider.transcribe", new=AsyncMock(return_value=transcript)),
            patch("backend.providers.edge_tts_provider.EdgeTTSProvider.speak", new=AsyncMock(return_value=FAKE_MP3)),
        ):
            t_resp = await ac.post("/api/transcribe", files={"audio": ("clip.wav", wav, "audio/wav")})
            assert t_resp.status_code == 200
            user_text = t_resp.json()["text"]
            assert user_text == transcript
            s_resp = await ac.post("/api/speak", json={"text": f"You said: {user_text}"})
            assert s_resp.status_code == 200
            assert s_resp.headers["content-type"] == "audio/mpeg"

    async def test_transcribe_response_schema(self, ac: httpx.AsyncClient, wav: bytes) -> None:
        with patch("backend.providers.whisper_provider.WhisperSTTProvider.transcribe", new=AsyncMock(return_value="hello world")):
            resp = await ac.post("/api/transcribe", files={"audio": ("clip.wav", wav, "audio/wav")})
        body = resp.json()
        assert resp.status_code == 200
        assert "text" in body and "display_text" in body and "duration_ms" in body
        assert isinstance(body["duration_ms"], float)

class TestMultiLanguage:
    async def test_hindi_transcribe_and_speak(self, ac: httpx.AsyncClient, wav: bytes) -> None:
        hindi_text = "Hello from Hindi"
        with (
            patch("backend.providers.whisper_provider.WhisperSTTProvider.transcribe", new=AsyncMock(return_value=hindi_text)),
            patch("backend.providers.edge_tts_provider.EdgeTTSProvider.speak", new=AsyncMock(return_value=FAKE_MP3)),
        ):
            t_resp = await ac.post("/api/transcribe?language=hi", files={"audio": ("hindi.wav", wav, "audio/wav")})
            assert t_resp.status_code == 200
            assert t_resp.json()["language"] == "hi"
            s_resp = await ac.post("/api/speak", json={"text": hindi_text, "language": "hi"})
            assert s_resp.status_code == 200

@pytest.fixture
def auth_app() -> FastAPI:
    settings = VocalTwistSettings(api_key_enabled=True, api_keys={"e2e-secret-key"}, rate_limit_enabled=False, log_level="WARNING", log_format="text")
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings
    return app

class TestApiKeyAuthentication:
    async def test_transcribe_without_key_returns_401(self, auth_app: FastAPI, wav: bytes) -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_app), base_url="http://testserver") as c:
            resp = await c.post("/api/transcribe", files={"audio": ("clip.wav", wav, "audio/wav")})
        assert resp.status_code == 401

    async def test_transcribe_with_wrong_key_returns_401(self, auth_app: FastAPI, wav: bytes) -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_app), base_url="http://testserver") as c:
            resp = await c.post("/api/transcribe", files={"audio": ("clip.wav", wav, "audio/wav")}, headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401

    async def test_transcribe_with_valid_key_succeeds(self, auth_app: FastAPI, wav: bytes) -> None:
        with patch("backend.providers.whisper_provider.WhisperSTTProvider.transcribe", new=AsyncMock(return_value="authenticated")):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_app), base_url="http://testserver") as c:
                resp = await c.post("/api/transcribe", files={"audio": ("clip.wav", wav, "audio/wav")}, headers={"X-API-Key": "e2e-secret-key"})
        assert resp.status_code == 200

    async def test_speak_without_key_returns_401(self, auth_app: FastAPI) -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_app), base_url="http://testserver") as c:
            resp = await c.post("/api/speak", json={"text": "hello"})
        assert resp.status_code == 401

    async def test_health_and_voices_are_public(self, auth_app: FastAPI) -> None:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=auth_app), base_url="http://testserver") as c:
            assert (await c.get("/api/health")).status_code == 200
            assert (await c.get("/api/voices")).status_code == 200

@pytest.fixture
def rate_app() -> FastAPI:
    settings = VocalTwistSettings(api_key_enabled=False, rate_limit_enabled=True, rate_limit_transcribe="3/minute", rate_limit_speak="3/minute", log_level="WARNING", log_format="text")
    # Reset module-level limiters so each fixture gets a fresh limiter.
    _mw._transcribe_limiter = None
    _mw._speak_limiter = None
    app = create_app(settings=settings)
    app.dependency_overrides[get_settings] = lambda: settings
    yield app
    # Tear down: reset limiters so they don't bleed into subsequent tests.
    _mw._transcribe_limiter = None
    _mw._speak_limiter = None

class TestRateLimiting:
    async def test_transcribe_429_after_limit_exhausted(self, rate_app: FastAPI, wav: bytes) -> None:
        with patch("backend.providers.whisper_provider.WhisperSTTProvider.transcribe", new=AsyncMock(return_value="ok")):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=rate_app), base_url="http://testserver") as c:
                for _ in range(3):
                    r = await c.post("/api/transcribe", files={"audio": ("clip.wav", wav, "audio/wav")})
                    assert r.status_code == 200
                over = await c.post("/api/transcribe", files={"audio": ("clip.wav", wav, "audio/wav")})
        assert over.status_code == 429
        assert "Retry-After" in over.headers

    async def test_different_ips_are_independent(self, rate_app: FastAPI, wav: bytes) -> None:
        with patch("backend.providers.whisper_provider.WhisperSTTProvider.transcribe", new=AsyncMock(return_value="ok")):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=rate_app), base_url="http://testserver") as c:
                for _ in range(3):
                    await c.post("/api/transcribe", files={"audio": ("c.wav", wav, "audio/wav")}, headers={"X-Forwarded-For": "10.0.0.1"})
                r1 = await c.post("/api/transcribe", files={"audio": ("c.wav", wav, "audio/wav")}, headers={"X-Forwarded-For": "10.0.0.1"})
                r2 = await c.post("/api/transcribe", files={"audio": ("c.wav", wav, "audio/wav")}, headers={"X-Forwarded-For": "10.0.0.2"})
        assert r1.status_code == 429
        assert r2.status_code == 200

class TestCorrelationIds:
    async def test_x_request_id_echoed(self, ac: httpx.AsyncClient) -> None:
        with patch("backend.providers.edge_tts_provider.EdgeTTSProvider.speak", new=AsyncMock(return_value=FAKE_MP3)):
            resp = await ac.post("/api/speak", json={"text": "Hello"}, headers={"X-Request-ID": "e2e-trace-abc-123"})
        assert resp.status_code == 200
        assert resp.headers.get("x-request-id") == "e2e-trace-abc-123"

    async def test_x_correlation_id_used_as_request_id(self, ac: httpx.AsyncClient) -> None:
        with patch("backend.providers.edge_tts_provider.EdgeTTSProvider.speak", new=AsyncMock(return_value=FAKE_MP3)):
            resp = await ac.post("/api/speak", json={"text": "Hello"}, headers={"X-Correlation-ID": "corr-xyz-999"})
        assert resp.status_code == 200
        assert resp.headers.get("x-request-id") == "corr-xyz-999"

    async def test_server_generates_id_when_none_supplied(self, ac: httpx.AsyncClient) -> None:
        with patch("backend.providers.edge_tts_provider.EdgeTTSProvider.speak", new=AsyncMock(return_value=FAKE_MP3)):
            resp = await ac.post("/api/speak", json={"text": "Hi"})
        assert resp.status_code == 200
        assert len(resp.headers.get("x-request-id", "")) > 0

class TestCorsHeaders:
    async def test_preflight_returns_success(self, ac: httpx.AsyncClient) -> None:
        resp = await ac.options("/api/health", headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "GET"})
        assert resp.status_code in (200, 204)

    async def test_get_has_allow_origin(self, ac: httpx.AsyncClient) -> None:
        resp = await ac.get("/api/health", headers={"Origin": "http://example.com"})
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers

    async def test_post_has_allow_origin(self, ac: httpx.AsyncClient) -> None:
        with patch("backend.providers.edge_tts_provider.EdgeTTSProvider.speak", new=AsyncMock(return_value=FAKE_MP3)):
            resp = await ac.post("/api/speak", json={"text": "Hello"}, headers={"Origin": "http://myapp.com"})
        assert resp.status_code == 200
        assert "access-control-allow-origin" in resp.headers

class TestConcurrentRequests:
    async def test_five_concurrent_speak_requests(self, ac: httpx.AsyncClient) -> None:
        with patch("backend.providers.edge_tts_provider.EdgeTTSProvider.speak", new=AsyncMock(return_value=FAKE_MP3)):
            responses = await asyncio.gather(*[ac.post("/api/speak", json={"text": f"Msg {i}"}) for i in range(5)])
        assert all(r.status_code == 200 for r in responses)

    async def test_concurrent_mixed_endpoints(self, ac: httpx.AsyncClient) -> None:
        with patch("backend.providers.edge_tts_provider.EdgeTTSProvider.speak", new=AsyncMock(return_value=FAKE_MP3)):
            results = await asyncio.gather(ac.get("/api/health"), ac.get("/api/voices"), ac.post("/api/speak", json={"text": "concurrent"}), ac.get("/api/providers"))
        assert all(r.status_code == 200 for r in results)

class TestAmbientTranscription:
    async def test_ambient_returns_slim_body(self, ac: httpx.AsyncClient, wav: bytes) -> None:
        with patch("backend.providers.whisper_provider.WhisperSTTProvider.transcribe", new=AsyncMock(return_value="the nurse walked in")):
            resp = await ac.post("/api/transcribe-ambient", files={"audio": ("ambient.wav", wav, "audio/wav")})
        assert resp.status_code == 200
        body = resp.json()
        assert "text" in body and "display_text" in body

    async def test_ambient_without_file_returns_422(self, ac: httpx.AsyncClient) -> None:
        assert (await ac.post("/api/transcribe-ambient")).status_code == 422

    async def test_ambient_with_language_hint(self, ac: httpx.AsyncClient, wav: bytes) -> None:
        with patch("backend.providers.whisper_provider.WhisperSTTProvider.transcribe", new=AsyncMock(return_value="bonjour")):
            resp = await ac.post("/api/transcribe-ambient?language=fr", files={"audio": ("fr.wav", wav, "audio/wav")})
        assert resp.status_code == 200

class TestErrorIsolation:
    async def test_stt_500_does_not_affect_speak(self, ac: httpx.AsyncClient, wav: bytes) -> None:
        with patch("backend.providers.whisper_provider.WhisperSTTProvider.transcribe", new=AsyncMock(side_effect=RuntimeError("GPU crash"))):
            assert (await ac.post("/api/transcribe", files={"audio": ("clip.wav", wav, "audio/wav")})).status_code == 500
        with patch("backend.providers.edge_tts_provider.EdgeTTSProvider.speak", new=AsyncMock(return_value=FAKE_MP3)):
            assert (await ac.post("/api/speak", json={"text": "Fallback"})).status_code == 200

    async def test_tts_500_does_not_affect_health(self, ac: httpx.AsyncClient) -> None:
        with patch("backend.providers.edge_tts_provider.EdgeTTSProvider.speak", new=AsyncMock(side_effect=RuntimeError("TTS outage"))):
            assert (await ac.post("/api/speak", json={"text": "test"})).status_code == 500
        resp = await ac.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    async def test_error_body_includes_request_id(self, ac: httpx.AsyncClient, wav: bytes) -> None:
        with patch("backend.providers.whisper_provider.WhisperSTTProvider.transcribe", new=AsyncMock(side_effect=RuntimeError("crash"))):
            resp = await ac.post("/api/transcribe", files={"audio": ("clip.wav", wav, "audio/wav")}, headers={"X-Request-ID": "trace-e2e-001"})
        assert resp.status_code == 500
        assert resp.json()["request_id"] == "trace-e2e-001"

class TestVoiceDiscoveryChain:
    async def test_discover_voice_then_use_in_speak(self, ac: httpx.AsyncClient) -> None:
        voices_resp = await ac.get("/api/voices")
        en_voice = voices_resp.json()["voices"]["en"][0]["name"]
        with patch("backend.providers.edge_tts_provider.EdgeTTSProvider.speak", new=AsyncMock(return_value=FAKE_MP3)) as mock_speak:
            resp = await ac.post("/api/speak", json={"text": "Using a discovered voice", "voice": en_voice})
        assert resp.status_code == 200
        _, kwargs = mock_speak.call_args
        assert kwargs.get("voice") == en_voice

    async def test_all_languages_have_voices(self, ac: httpx.AsyncClient) -> None:
        voices = (await ac.get("/api/voices")).json()["voices"]
        for lang, voice_list in voices.items():
            assert len(voice_list) >= 1, f"No voices for {lang!r}"
            for v in voice_list:
                assert v.get("name") and v.get("language") == lang

class TestResponseHeaders:
    async def test_speak_headers_complete(self, ac: httpx.AsyncClient) -> None:
        with patch("backend.providers.edge_tts_provider.EdgeTTSProvider.speak", new=AsyncMock(return_value=FAKE_MP3)):
            resp = await ac.post("/api/speak", json={"text": "header test"}, headers={"X-Request-ID": "hdr-e2e-1"})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mpeg"
        assert "inline" in resp.headers.get("content-disposition", "")
        assert resp.headers.get("x-request-id") == "hdr-e2e-1"
        assert float(resp.headers.get("x-duration-ms", "-1")) >= 0.0

    async def test_health_schema_complete(self, ac: httpx.AsyncClient) -> None:
        resp = await ac.get("/api/health")
        body = resp.json()
        required = {"status", "version", "uptime_s", "stt_provider", "tts_provider"}
        assert required.issubset(body.keys())
        assert isinstance(body["uptime_s"], (int, float))

class TestInputSanitisation:
    async def test_xss_stripped_before_tts(self, ac: httpx.AsyncClient) -> None:
        xss = '<img src=x onerror=alert(1)>Hello<script>evil()</script>'
        with patch("backend.providers.edge_tts_provider.EdgeTTSProvider.speak", new=AsyncMock(return_value=FAKE_MP3)) as mock_speak:
            resp = await ac.post("/api/speak", json={"text": xss})
        assert resp.status_code == 200
        called_text = mock_speak.call_args[0][0] if mock_speak.call_args[0] else mock_speak.call_args[1].get("text", "")
        assert "<" not in called_text
        assert "script" not in called_text.lower()

    async def test_oversized_audio_returns_400(self, ac: httpx.AsyncClient, e2e_settings: VocalTwistSettings) -> None:
        oversized = b"\x00" * (e2e_settings.max_audio_bytes + 1)
        resp = await ac.post("/api/transcribe", files={"audio": ("huge.wav", oversized, "audio/wav")})
        assert resp.status_code == 400
        assert "exceeds" in resp.json()["detail"]

    async def test_non_audio_mime_rejected(self, ac: httpx.AsyncClient) -> None:
        resp = await ac.post("/api/transcribe", files={"audio": ("evil.php", b"<?php ?>", "application/x-php")})
        assert resp.status_code == 400
        assert "Unsupported audio type" in resp.json()["detail"]

    async def test_empty_speak_text_returns_422(self, ac: httpx.AsyncClient) -> None:
        assert (await ac.post("/api/speak", json={"text": ""})).status_code == 422

    async def test_html_only_speak_text_rejected(self, ac: httpx.AsyncClient) -> None:
        resp = await ac.post("/api/speak", json={"text": "<b></b><i></i>"})
        assert resp.status_code in (400, 422)

    async def test_invalid_task_param_returns_422(self, ac: httpx.AsyncClient, wav: bytes) -> None:
        with patch("backend.providers.whisper_provider.WhisperSTTProvider.transcribe", new=AsyncMock(return_value="")):
            resp = await ac.post("/api/transcribe?task=malicious", files={"audio": ("clip.wav", wav, "audio/wav")})
        assert resp.status_code == 422
