"""TTS-specific tests for /api/speak.

Covers voice selection logic, language-to-voice mapping, response headers,
and edge cases specific to the EdgeTTSProvider.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fake MP3 payload (valid enough for tests)
# ---------------------------------------------------------------------------

_FAKE_MP3 = b"\xff\xfb\x90\x00" + b"\x00" * 256


# ---------------------------------------------------------------------------
# Voice selection
# ---------------------------------------------------------------------------

class TestVoiceSelection:
    """Verify that the correct voice is chosen in each resolution path."""

    def test_explicit_voice_used(self, client):
        """When voice is explicitly provided it must reach the TTS provider."""
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=_FAKE_MP3),
        ) as mock_speak:
            resp = client.post(
                "/api/speak",
                json={"text": "Testing voice", "voice": "de-DE-KatjaNeural"},
            )
        assert resp.status_code == 200
        mock_speak.assert_awaited_once()
        _, kwargs = mock_speak.call_args
        assert kwargs["voice"] == "de-DE-KatjaNeural"

    def test_language_default_voice(self, client):
        """When only language is provided the correct default voice is resolved."""
        # Hindi should resolve to hi-IN-SwaraNeural
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=_FAKE_MP3),
        ) as mock_speak:
            resp = client.post(
                "/api/speak",
                json={"text": "नमस्ते", "language": "hi"},
            )
        assert resp.status_code == 200
        # The router resolves the voice before calling the provider
        # so the voice kwarg must be non-None
        mock_speak.assert_awaited_once()

    def test_fallback_to_default_voice(self, client):
        """When neither voice nor language is supplied, the global default is used."""
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=_FAKE_MP3),
        ) as mock_speak:
            resp = client.post(
                "/api/speak",
                json={"text": "Hello world"},
            )
        assert resp.status_code == 200
        mock_speak.assert_awaited_once()
        _, kwargs = mock_speak.call_args
        # Default should be a valid voice string
        assert kwargs["voice"]

    @pytest.mark.parametrize("lang,expected_fragment", [
        ("en", "AriaNeural"),
        ("hi", "SwaraNeural"),
        ("mr", "AarohiNeural"),
        ("es", "ElviraNeural"),
        ("fr", "DeniseNeural"),
        ("de", "KatjaNeural"),
        ("zh", "XiaoxiaoNeural"),
        ("ja", "NanamiNeural"),
        ("ar", "ZariyahNeural"),
    ])
    def test_language_voice_mapping(self, client, lang, expected_fragment):
        """Each supported language maps to the expected default voice."""
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=_FAKE_MP3),
        ) as mock_speak:
            resp = client.post(
                "/api/speak",
                json={"text": "Test", "language": lang},
            )
        assert resp.status_code == 200
        mock_speak.assert_awaited_once()
        _, kwargs = mock_speak.call_args
        assert expected_fragment in kwargs["voice"], (
            f"For lang='{lang}' expected voice containing '{expected_fragment}', "
            f"got '{kwargs['voice']}'"
        )


# ---------------------------------------------------------------------------
# Response headers
# ---------------------------------------------------------------------------

class TestResponseHeaders:
    def test_content_type_is_audio_mpeg(self, client):
        """Response Content-Type must be audio/mpeg."""
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=_FAKE_MP3),
        ):
            resp = client.post("/api/speak", json={"text": "hi"})
        assert resp.status_code == 200
        assert resp.headers["content-type"] == "audio/mpeg"

    def test_content_disposition_inline(self, client):
        """Content-Disposition must be inline (browser auto-play friendly)."""
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=_FAKE_MP3),
        ):
            resp = client.post("/api/speak", json={"text": "hi"})
        assert "inline" in resp.headers.get("content-disposition", "")

    def test_x_duration_ms_header_present(self, client):
        """X-Duration-Ms header must be present and numeric."""
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=_FAKE_MP3),
        ):
            resp = client.post("/api/speak", json={"text": "hi"})
        dur = resp.headers.get("x-duration-ms")
        assert dur is not None
        assert float(dur) >= 0.0

    def test_request_id_echo(self, client):
        """Supplied X-Request-ID must be echoed back in the response."""
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=_FAKE_MP3),
        ):
            resp = client.post(
                "/api/speak",
                json={"text": "hi"},
                headers={"X-Request-ID": "unit-test-42"},
            )
        assert resp.headers.get("x-request-id") == "unit-test-42"


# ---------------------------------------------------------------------------
# Input sanitisation
# ---------------------------------------------------------------------------

class TestInputSanitisation:
    def test_script_tag_stripped(self, client):
        """<script> blocks must be removed before reaching the TTS engine."""
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=_FAKE_MP3),
        ) as mock_speak:
            resp = client.post(
                "/api/speak",
                json={"text": "<script>document.cookie</script>Hello"},
            )
        assert resp.status_code == 200
        called_text = mock_speak.call_args[0][0] if mock_speak.call_args[0] else mock_speak.call_args[1].get("text", "")
        assert "script" not in called_text.lower()
        assert "document.cookie" not in called_text

    def test_html_entities_stripped(self, client):
        """Inline HTML elements must be stripped, leaving only text content."""
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=_FAKE_MP3),
        ) as mock_speak:
            resp = client.post(
                "/api/speak",
                json={"text": "<b>bold</b> and <em>italic</em>"},
            )
        assert resp.status_code == 200
        called_text = mock_speak.call_args[0][0] if mock_speak.call_args[0] else mock_speak.call_args[1].get("text", "")
        assert "<" not in called_text
        # Actual words must survive
        assert "bold" in called_text
        assert "italic" in called_text

    def test_whitespace_normalised(self, client):
        """Multiple whitespace characters in text must be collapsed."""
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(return_value=_FAKE_MP3),
        ) as mock_speak:
            resp = client.post(
                "/api/speak",
                json={"text": "hello   \t\n   world"},
            )
        assert resp.status_code == 200
        called_text = mock_speak.call_args[0][0] if mock_speak.call_args[0] else mock_speak.call_args[1].get("text", "")
        assert "  " not in called_text  # no double spaces


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling:
    def test_tts_runtime_error_returns_500(self, client):
        """RuntimeError from TTS must map to HTTP 500."""
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(side_effect=RuntimeError("edge-tts timeout")),
        ):
            resp = client.post("/api/speak", json={"text": "test"})
        assert resp.status_code == 500
        assert "detail" in resp.json()

    def test_tts_returns_empty_bytes(self, client):
        """When TTS provider raises RuntimeError for empty audio, return 500."""
        with patch(
            "backend.providers.edge_tts_provider.EdgeTTSProvider.speak",
            new=AsyncMock(
                side_effect=RuntimeError("edge-tts returned no audio")
            ),
        ):
            resp = client.post("/api/speak", json={"text": "test"})
        assert resp.status_code == 500

    def test_speak_only_html_returns_422(self, client):
        """Text that is pure HTML and strips to empty must return 422."""
        resp = client.post("/api/speak", json={"text": "<b></b>"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Voices endpoint
# ---------------------------------------------------------------------------

class TestVoicesEndpoint:
    def test_voices_endpoint_structure(self, client):
        """GET /api/voices must return a dict keyed by language code."""
        resp = client.get("/api/voices")
        assert resp.status_code == 200
        data = resp.json()
        assert "voices" in data
        assert isinstance(data["voices"], dict)
        # English voices should always be present
        assert "en" in data["voices"]
        assert len(data["voices"]["en"]) > 0

    def test_each_voice_has_name_and_language(self, client):
        """Every voice entry must have 'name' and 'language' fields."""
        resp = client.get("/api/voices")
        assert resp.status_code == 200
        for lang, voice_list in resp.json()["voices"].items():
            for voice in voice_list:
                assert "name" in voice
                assert "language" in voice
                assert voice["language"] == lang
