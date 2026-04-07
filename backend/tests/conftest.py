"""Pytest fixtures shared across all VocalTwist backend tests."""
from __future__ import annotations

import io
import struct
import wave

import pytest
from fastapi.testclient import TestClient

from backend.config import VocalTwistSettings
from backend.middleware import create_app


# ---------------------------------------------------------------------------
# Settings fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def settings() -> VocalTwistSettings:
    """Return a VocalTwistSettings instance pre-configured for testing.

    Key overrides:
    * API-key enforcement disabled (tests don't need to supply a key).
    * Rate limiting disabled (avoids flaky 429s in fast test runs).
    * Log level set to WARNING to keep test output clean.
    * Log format set to text for human-readable pytest output.
    """
    return VocalTwistSettings(
        api_key_enabled=False,
        rate_limit_enabled=False,
        log_level="WARNING",
        log_format="text",
        stt_provider="whisper",
        tts_provider="edge_tts",
    )


# ---------------------------------------------------------------------------
# TestClient fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def client(settings: VocalTwistSettings) -> TestClient:
    """Return a synchronous FastAPI TestClient backed by a test-configured app."""
    app = create_app(settings=settings)
    return TestClient(app, raise_server_exceptions=True)


# ---------------------------------------------------------------------------
# Sample audio fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def sample_wav_bytes() -> bytes:
    """Generate a minimal valid WAV file containing 1 second of silence at 16 kHz.

    The WAV is constructed entirely in memory — no filesystem writes required.
    The result is a valid RIFF/PCM file that faster-whisper (and ffmpeg) can
    decode without issues.
    """
    sample_rate = 16_000  # Hz
    num_channels = 1       # Mono
    sampwidth = 2          # 16-bit PCM
    num_frames = sample_rate  # 1 second of silence

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(num_channels)
        wf.setsampwidth(sampwidth)
        wf.setframerate(sample_rate)
        wf.writeframes(b"\x00" * num_frames * num_channels * sampwidth)

    return buf.getvalue()


# ---------------------------------------------------------------------------
# Sample text fixture
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def sample_text() -> str:
    """A short, safe sentence for TTS tests."""
    return "Hello, this is a test message."
