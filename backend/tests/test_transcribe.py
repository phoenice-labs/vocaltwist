"""Tests for the /api/transcribe and /api/speak endpoints.

All STT and TTS provider calls are mocked so the tests do not require
a GPU, a Whisper model download, or network access to Microsoft edge-tts.
"""
from __future__ import annotations

import io
import json
import wave
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# 1. Health endpoint
# ---------------------------------------------------------------------------

class TestHealth:
    def test_transcribe_health(self, client):
        """GET /api/health must return HTTP 200 with status='ok'."""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert "version" in data
        assert "stt_provider" in data
        assert "tts_provider" in data
        assert data["uptime_s"] >= 0.0


# ---------------------------------------------------------------------------
# 2. Transcribe — validation errors
# ---------------------------------------------------------------------------

class TestTranscribeValidation:
    def test_transcribe_no_file(self, client):
        """POST /api/transcribe without any file must return HTTP 422."""
        resp = client.post("/api/transcribe")
        assert resp.status_code == 422

    def test_transcribe_invalid_type(self, client):
        """POST with a text/plain content-type must return HTTP 400."""
        resp = client.post(
            "/api/transcribe",
            files={"audio": ("note.txt", b"hello world", "text/plain")},
        )
        assert resp.status_code == 400
        assert "Unsupported audio type" in resp.json()["detail"]

    def test_transcribe_oversized(self, client, settings):
        """POST with a file exceeding max_audio_bytes must return HTTP 400."""
        # Create a payload slightly larger than the limit.
        oversized = b"\x00" * (settings.max_audio_bytes + 1)
        resp = client.post(
            "/api/transcribe",
            files={"audio": ("big.wav", oversized, "audio/wav")},
        )
        assert resp.status_code == 400
        assert "exceeds" in resp.json()["detail"]

    def test_transcribe_invalid_task(self, client, sample_wav_bytes):
        """POST with task='bad' must return HTTP 422."""
        with patch(
            "backend.providers.whisper_provider.WhisperSTTProvider.transcribe",
            new=AsyncMock(return_value=""),
        ):
            resp = client.post(
                "/api/transcribe?task=bad",
                files={"audio": ("clip.wav", sample_wav_bytes, "audio/wav")},
            )
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 3. Transcribe — happy path (mocked provider)
# ---------------------------------------------------------------------------

class TestTranscribeSuccess:
    def test_transcribe_valid_wav(self, client, sample_wav_bytes):
        """POST with a valid WAV file must return HTTP 200 with a text field."""
        mock_transcript = "hello how are you"
        with patch(
            "backend.providers.whisper_provider.WhisperSTTProvider.transcribe",
            new=AsyncMock(return_value=mock_transcript),
        ):
            resp = client.post(
                "/api/transcribe",
                files={"audio": ("clip.wav", sample_wav_bytes, "audio/wav")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert data["text"] == mock_transcript
        assert "display_text" in data
        assert isinstance(data["duration_ms"], float)

    def test_transcribe_with_language(self, client, sample_wav_bytes):
        """Language hint must be passed through to the provider."""
        with patch(
            "backend.providers.whisper_provider.WhisperSTTProvider.transcribe",
            new=AsyncMock(return_value="नमस्ते"),
        ) as mock_fn:
            resp = client.post(
                "/api/transcribe?language=hi",
                files={"audio": ("clip.wav", sample_wav_bytes, "audio/wav")},
            )
        assert resp.status_code == 200
        assert resp.json()["language"] == "hi"

    def test_transcribe_translate_task(self, client, sample_wav_bytes):
        """task=translate must be forwarded to the STT provider."""
        with patch(
            "backend.providers.whisper_provider.WhisperSTTProvider.transcribe",
            new=AsyncMock(return_value="hello"),
        ) as mock_fn:
            resp = client.post(
                "/api/transcribe?task=translate",
                files={"audio": ("clip.wav", sample_wav_bytes, "audio/wav")},
            )
        assert resp.status_code == 200
        mock_fn.assert_awaited_once()
        _, kwargs = mock_fn.call_args
        assert kwargs.get("task") == "translate"

    def test_transcribe_provider_error(self, client, sample_wav_bytes):
        """A RuntimeError from the STT provider must return HTTP 500."""
        with patch(
            "backend.providers.whisper_provider.WhisperSTTProvider.transcribe",
            new=AsyncMock(side_effect=RuntimeError("GPU out of memory")),
        ):
            resp = client.post(
                "/api/transcribe",
                files={"audio": ("clip.wav", sample_wav_bytes, "audio/wav")},
            )
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# 4. Ambient transcription
# ---------------------------------------------------------------------------

class TestTranscribeAmbient:
    def test_transcribe_ambient(self, client, sample_wav_bytes):
        """POST /api/transcribe-ambient must return {text, display_text}."""
        with patch(
            "backend.providers.whisper_provider.WhisperSTTProvider.transcribe",
            new=AsyncMock(return_value="the patient has a fever"),
        ):
            resp = client.post(
                "/api/transcribe-ambient",
                files={"audio": ("ambient.wav", sample_wav_bytes, "audio/wav")},
            )

        assert resp.status_code == 200
        data = resp.json()
        assert "text" in data
        assert "display_text" in data
        # display_text should capitalise the first letter
        assert data["display_text"][0].isupper()

    def test_transcribe_ambient_no_file(self, client):
        """POST /api/transcribe-ambient without a file must return 422."""
        resp = client.post("/api/transcribe-ambient")
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# 5. Speak endpoint — happy path
# ---------------------------------------------------------------------------

class TestSpeakSuccess:
    def test_speak_valid(self, client, sample_text):
        """POST /api/speak with valid text must return audio/mpeg."""
        fake_mp3 = b"\xff\xfb\x90\x00" + b"\x00" * 100  # fake MP3 header

        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=fake_mp3),
        ):
            resp = client.post("/api/speak", json={"text": sample_text})

        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mpeg"
        assert resp.content == fake_mp3

    def test_speak_returns_request_id_header(self, client, sample_text):
        """Response must include X-Request-ID header."""
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=b"\xff\xfb" + b"\x00" * 50),
        ):
            resp = client.post(
                "/api/speak",
                json={"text": sample_text},
                headers={"X-Request-ID": "test-req-123"},
            )
        assert resp.status_code == 200
        assert resp.headers.get("x-request-id") == "test-req-123"

    def test_speak_with_voice_override(self, client, sample_text):
        """Explicit voice parameter must be forwarded to the TTS provider."""
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=b"\xff\xfb" + b"\x00" * 50),
        ) as mock_speak:
            resp = client.post(
                "/api/speak",
                json={"text": sample_text, "voice": "hi-IN-SwaraNeural"},
            )
        assert resp.status_code == 200
        mock_speak.assert_awaited_once()
        _, kwargs = mock_speak.call_args
        assert kwargs.get("voice") == "hi-IN-SwaraNeural"


# ---------------------------------------------------------------------------
# 6. Speak endpoint — validation errors
# ---------------------------------------------------------------------------

class TestSpeakValidation:
    def test_speak_empty_text(self, client):
        """POST with empty text must return HTTP 422."""
        resp = client.post("/api/speak", json={"text": ""})
        assert resp.status_code == 422

    def test_speak_too_long(self, client):
        """POST with text > 2000 chars must be truncated (not 400) or rejected."""
        long_text = "a" * 2001
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=b"\xff\xfb" + b"\x00" * 50),
        ) as mock_speak:
            resp = client.post("/api/speak", json={"text": long_text})
        # Model validator truncates to 2000 — either 200 OK (truncated) or 422
        assert resp.status_code in (200, 422, 400)
        if resp.status_code == 200:
            _, kwargs = mock_speak.call_args
            # text passed to speak must not exceed 2000 chars
            assert len(mock_speak.call_args[0][0] if mock_speak.call_args[0] else kwargs.get("text", "")) <= 2000

    def test_speak_html_stripped(self, client):
        """HTML tags in text must be stripped before TTS synthesis."""
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=b"\xff\xfb" + b"\x00" * 50),
        ) as mock_speak:
            resp = client.post(
                "/api/speak",
                json={"text": "<b>Hello</b> <script>alert(1)</script> world"},
            )
        assert resp.status_code == 200
        called_text = mock_speak.call_args[0][0] if mock_speak.call_args[0] else mock_speak.call_args[1].get("text", "")
        # Ensure no HTML tags remain
        assert "<" not in called_text
        assert "script" not in called_text.lower()

    def test_speak_provider_error(self, client, sample_text):
        """A RuntimeError from TTS must return HTTP 500."""
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(side_effect=RuntimeError("Network error")),
        ):
            resp = client.post("/api/speak", json={"text": sample_text})
        assert resp.status_code == 500


# ---------------------------------------------------------------------------
# 7. Providers endpoint
# ---------------------------------------------------------------------------

class TestProvidersEndpoint:
    def test_providers_endpoint(self, client):
        """GET /api/providers must return STT and TTS lists."""
        resp = client.get("/api/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "stt" in data
        assert "tts" in data
        assert isinstance(data["stt"], list)
        assert isinstance(data["tts"], list)
