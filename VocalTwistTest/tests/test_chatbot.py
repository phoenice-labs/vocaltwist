"""Integration tests for VocalTwistTest demo chatbot.

Run with:
    pytest VocalTwistTest/tests/ -v

LM Studio is NOT required — LLM calls are mocked.
"""
from __future__ import annotations

import io
import struct
import wave
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Ensure the backend package is importable from this test file.
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from VocalTwistTest.app import app  # noqa: E402


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_minimal_wav(duration_secs: float = 0.1, sample_rate: int = 16_000) -> bytes:
    """Return a minimal mono 16-bit PCM WAV byte string."""
    n_samples = int(sample_rate * duration_secs)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{n_samples}h", *([0] * n_samples)))
    return buf.getvalue()


@pytest.fixture(scope="module")
def client() -> TestClient:
    return TestClient(app, raise_server_exceptions=True)


# ── Tests ──────────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_returns_200(self, client: TestClient) -> None:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body.get("status") in {"ok", "healthy", "degraded"}


class TestChat:
    def test_chat_no_messages_returns_422(self, client: TestClient) -> None:
        resp = client.post("/api/chat", json={"messages": [], "language": "en"})
        assert resp.status_code == 422

    def test_chat_missing_messages_field_returns_422(self, client: TestClient) -> None:
        resp = client.post("/api/chat", json={"language": "en"})
        assert resp.status_code == 422

    def test_chat_invalid_role_returns_422(self, client: TestClient) -> None:
        resp = client.post(
            "/api/chat",
            json={"messages": [{"role": "robot", "content": "hi"}], "language": "en"},
        )
        assert resp.status_code == 422

    def test_chat_with_llm_down_returns_200_fallback(self, client: TestClient) -> None:
        """When LM Studio is unreachable the endpoint must NOT raise 500.

        It should return HTTP 200 with a friendly fallback message.
        """
        import httpx as _httpx

        with patch("VocalTwistTest.app._http_client") as mock_client:
            mock_client.post = AsyncMock(
                side_effect=_httpx.ConnectError("Connection refused")
            )
            # Reset circuit-breaker state so it actually calls the client
            import VocalTwistTest.app as _app
            _app._cb_failures = 0

            resp = client.post(
                "/api/chat",
                json={
                    "messages": [{"role": "user", "content": "Hello"}],
                    "language": "en",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert "reply" in body
        assert "is_emergency" in body
        assert isinstance(body["reply"], str)
        assert len(body["reply"]) > 0

    def test_chat_success_with_mocked_llm(self, client: TestClient) -> None:
        llm_payload = {
            "choices": [
                {"message": {"content": "Hello! How can I help you today?"}}
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = llm_payload
        mock_resp.raise_for_status.return_value = None

        import VocalTwistTest.app as _app
        _app._cb_failures = 0

        with patch("VocalTwistTest.app._http_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_resp)
            resp = client.post(
                "/api/chat",
                json={
                    "messages": [{"role": "user", "content": "Hi there"}],
                    "language": "en",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["reply"] == "Hello! How can I help you today?"
        assert body["is_emergency"] is False

    def test_chat_emergency_detection(self, client: TestClient) -> None:
        llm_payload = {
            "choices": [
                {"message": {"content": "This is an emergency! Call 911 immediately."}}
            ]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = llm_payload
        mock_resp.raise_for_status.return_value = None

        import VocalTwistTest.app as _app
        _app._cb_failures = 0

        with patch("VocalTwistTest.app._http_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_resp)
            resp = client.post(
                "/api/chat",
                json={
                    "messages": [{"role": "user", "content": "Someone is hurt!"}],
                    "language": "en",
                },
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["is_emergency"] is True

    def test_chat_circuit_breaker_open_returns_fallback(
        self, client: TestClient
    ) -> None:
        import VocalTwistTest.app as _app

        original_failures = _app._cb_failures
        original_last = _app._cb_last_failure
        try:
            _app._cb_failures = _app._CB_THRESHOLD
            _app._cb_last_failure = _app.time.monotonic()  # just now → breaker open

            resp = client.post(
                "/api/chat",
                json={
                    "messages": [{"role": "user", "content": "Hello"}],
                    "language": "en",
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert "reply" in body
        finally:
            _app._cb_failures = original_failures
            _app._cb_last_failure = original_last

    def test_chat_rate_limit(self, client: TestClient) -> None:
        """Sending more than _RATE_LIMIT requests quickly should yield 429."""
        import VocalTwistTest.app as _app

        llm_payload = {
            "choices": [{"message": {"content": "OK"}}]
        }
        mock_resp = MagicMock()
        mock_resp.json.return_value = llm_payload
        mock_resp.raise_for_status.return_value = None
        _app._cb_failures = 0

        # Clear any existing rate-limit state for the test client IP
        test_ip = "testclient"
        _app._rate_store.pop(test_ip, None)
        # Force the store to the limit
        import time as _time
        _app._rate_store[test_ip] = [_time.monotonic()] * _app._RATE_LIMIT

        with patch("VocalTwistTest.app._http_client") as mock_client:
            mock_client.post = AsyncMock(return_value=mock_resp)
            resp = client.post(
                "/api/chat",
                json={
                    "messages": [{"role": "user", "content": "hi"}],
                    "language": "en",
                },
                headers={"x-forwarded-for": test_ip},
            )

        # Rate limiter uses request.client.host which TestClient sets to "testclient"
        assert resp.status_code == 429


class TestTranscribe:
    def test_transcribe_integration(self, client: TestClient) -> None:
        """POST /api/transcribe with a minimal WAV must return a JSON body.

        The actual STT model may not be loaded in CI, so we accept either
        a successful transcription (200) or a 503/500 with an error body.
        """
        wav_bytes = _make_minimal_wav()
        resp = client.post(
            "/api/transcribe",
            files={"audio": ("test.wav", wav_bytes, "audio/wav")},
        )
        assert resp.status_code in {200, 422, 500, 503}
        if resp.status_code == 200:
            body = resp.json()
            assert "text" in body
            assert "language" in body


class TestSpeak:
    def test_speak_integration(self, client: TestClient) -> None:
        """POST /api/speak with short text must return audio/mpeg or an error.

        edge-tts requires network access; in offline CI we accept 500/503.
        """
        resp = client.post(
            "/api/speak",
            json={"text": "Hello", "language": "en"},
        )
        assert resp.status_code in {200, 500, 503}
        if resp.status_code == 200:
            assert "audio" in resp.headers.get("content-type", "")


class TestStatic:
    def test_get_root_returns_html(self, client: TestClient) -> None:
        resp = client.get("/")
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "html" in ct

    def test_chatbot_js_served(self, client: TestClient) -> None:
        resp = client.get("/chatbot.js")
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "javascript" in ct

    def test_vocal_twist_js_served(self, client: TestClient) -> None:
        resp = client.get("/vocal-twist.js")
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "javascript" in ct
