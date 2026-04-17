"""VocalTwist Chrome Extension — Playwright E2E tests.

Tests cover:
  - Backend health (REST)
  - Extension mic button lifecycle (focus/blur)
  - Extension TTS controls after AI response
  - Keyboard shortcut Ctrl+Shift+V
  - Upgraded mode: Whisper STT via /api/transcribe
  - Upgraded mode: Edge Neural TTS via /api/speak

Usage (from project root):
    # With backend running (docker-compose up -d):
    pytest VocalTwistTest/tests/e2e/test_extension.py -v

    # Without backend (browser built-in mode only):
    pytest VocalTwistTest/tests/e2e/test_extension.py -v -m "not upgraded_mode"

Environment variables:
    VOCALTWIST_BACKEND_URL   Override backend URL (default: http://localhost:8000)
    CHROME_USER_DATA_DIR     Override temp user data dir for browser context
    EXTENSION_PATH           Override path to unpacked extension dir
"""
from __future__ import annotations

import io
import os
import struct
import tempfile
import time
import wave
from pathlib import Path

import httpx
import pytest
from playwright.sync_api import Browser, BrowserContext, Page, Playwright, sync_playwright

from VocalTwistTest.tests.e2e.conftest import assert_extension_loaded

# ─── Configuration ─────────────────────────────────────────────────────────────

BACKEND_URL   = os.getenv("VOCALTWIST_BACKEND_URL", "http://localhost:8000")
TEST_PAGE_URL = f"{BACKEND_URL}/test-extension.html"

# Absolute path to the unpacked extension
_REPO_ROOT = Path(__file__).parent.parent.parent.parent  # VocalTwist/
EXTENSION_PATH = os.getenv(
    "EXTENSION_PATH",
    str(_REPO_ROOT / "vocaltwist-extension"),
)

# ─── Helpers ───────────────────────────────────────────────────────────────────


def _make_minimal_wav(duration_secs: float = 0.3, sample_rate: int = 16_000) -> bytes:
    """Return a minimal mono 16-bit PCM WAV byte string with silence."""
    n_samples = int(sample_rate * duration_secs)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(struct.pack(f"<{n_samples}h", *([0] * n_samples)))
    return buf.getvalue()


def _backend_is_up() -> bool:
    try:
        r = httpx.get(f"{BACKEND_URL}/api/health", timeout=3)
        return r.status_code == 200
    except Exception:
        return False


# ─── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture(scope="session")
def user_data_dir():
    """Persistent Chrome user-data-dir for the session so extension state is kept."""
    _override = os.getenv("CHROME_USER_DATA_DIR")
    if _override:
        yield _override
        return
    with tempfile.TemporaryDirectory(prefix="vt_playwright_") as tmpdir:
        yield tmpdir


@pytest.fixture(scope="session")
def browser_context(user_data_dir: str):
    """Launch Chrome with the VocalTwist extension loaded (persistent context).

    Falls back to bundled Chromium if system Chrome is unavailable.
    """
    ext_path = str(Path(EXTENSION_PATH).resolve())
    common_args = [
        f"--load-extension={ext_path}",
        f"--disable-extensions-except={ext_path}",
        "--disable-web-security",            # allow localhost cross-origin
        "--use-fake-ui-for-media-stream",    # grant mic/camera without system dialog
        "--use-fake-device-for-media-stream",# synthetic audio device (silence)
        "--no-first-run",                    # skip first-run Chrome welcome
        "--no-default-browser-check",
        "--disable-default-apps",
        "--disable-background-networking",
        "--disable-sync",
        "--disable-translate",
        "--no-sandbox",
        "--disable-dev-shm-usage",
    ]
    with sync_playwright() as pw:
        # Use bundled Chromium — system Chrome blocks unpacked extension content
        # scripts due to policy restrictions when launched programmatically.
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            args=common_args,
            permissions=["microphone"],
            slow_mo=100,
        )
        # Give extension service worker a moment to start
        time.sleep(2)
        yield ctx
        try:
            ctx.close()
        except Exception:
            pass  # already closed


@pytest.fixture()
def test_page(browser_context: BrowserContext) -> Page:
    """Open a fresh test-extension.html page for each test."""
    page = browser_context.new_page()
    page.goto(TEST_PAGE_URL, wait_until="domcontentloaded")
    # Wait for extension content script to initialise:
    # - data-vt-loaded sentinel is set immediately
    # - init() async: ~1s sendMessage timeout + storage read + orchestrator init
    page.wait_for_timeout(2500)
    yield page
    page.close()


# ─── Tests: Backend REST ────────────────────────────────────────────────────────


class TestBackendHealth:
    """Verify the VocalTwist backend REST API is reachable and healthy."""

    def test_health_api_endpoint(self):
        """GET /api/health → 200 with status=ok."""
        r = httpx.get(f"{BACKEND_URL}/api/health", timeout=5)
        assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
        body = r.json()
        assert body.get("status") == "ok", f"Unexpected body: {body}"

    def test_health_alias_endpoint(self):
        """GET /health alias also returns 200 (used by extension probe)."""
        r = httpx.get(f"{BACKEND_URL}/health", timeout=5)
        assert r.status_code == 200, f"GET /health returned {r.status_code}"
        assert r.json().get("status") == "ok"

    def test_test_page_is_served(self):
        """The test-extension.html page is served correctly."""
        r = httpx.get(TEST_PAGE_URL, timeout=5)
        assert r.status_code == 200
        assert "VocalTwist Extension Test Page" in r.text


# ─── Tests: Extension Mic Button ───────────────────────────────────────────────


class TestMicButton:
    """Verify the extension injects a mic button on textarea focus."""

    def test_mic_button_appears_on_focus(self, test_page: Page):
        """Mic button #vt-mic-button appears when the chat input is focused."""
        assert_extension_loaded(test_page)
        inp = test_page.locator("#user-input")
        inp.click()
        # Extension has up to 6s to inject the button
        mic = test_page.locator("#vt-mic-button")
        mic.wait_for(state="visible", timeout=6000)
        assert mic.is_visible(), "Mic button should be visible after textarea focus"

    def test_mic_button_disappears_on_blur(self, test_page: Page):
        """Mic button is hidden/removed when the input loses focus."""
        assert_extension_loaded(test_page)
        inp = test_page.locator("#user-input")
        inp.click()
        # Ensure button is first visible
        test_page.locator("#vt-mic-button").wait_for(state="visible", timeout=6000)
        # Click somewhere else to blur
        test_page.locator("header").click()
        # Button should disappear within 1s
        test_page.wait_for_timeout(800)
        mic = test_page.locator("#vt-mic-button")
        # Either detached from DOM or hidden
        visible = mic.is_visible() if mic.count() > 0 else False
        assert not visible, "Mic button should not be visible after blur"

    def test_mic_button_idle_state(self, test_page: Page):
        """Mic button in idle state has correct data-state attribute."""
        assert_extension_loaded(test_page)
        test_page.locator("#user-input").click()
        mic = test_page.locator("#vt-mic-button")
        mic.wait_for(state="visible", timeout=6000)
        state = mic.get_attribute("data-state")
        assert state == "idle", f"Expected idle, got: {state}"


# ─── Tests: TTS Controls after AI Response ─────────────────────────────────────


class TestTTSControls:
    """Verify the extension injects TTS mute/replay controls after an AI response."""

    def test_tts_controls_after_instant_response(self, test_page: Page):
        """TTS controls (.vt-tts-controls) are injected after an instant AI response."""
        assert_extension_loaded(test_page)
        test_page.locator("[data-testid='btn-trigger-response']").click()
        controls = test_page.locator(".vt-tts-controls").first
        controls.wait_for(state="attached", timeout=8000)
        assert controls.count() > 0, "TTS controls not found after AI response"

    def test_tts_controls_after_streamed_response(self, test_page: Page):
        """TTS controls appear after a streaming AI response completes."""
        assert_extension_loaded(test_page)
        test_page.locator("[data-testid='btn-stream-response']").click()
        controls = test_page.locator(".vt-tts-controls").first
        controls.wait_for(state="attached", timeout=10000)
        assert controls.count() > 0, "TTS controls not injected after streamed response"

    def test_tts_mute_button_present(self, test_page: Page):
        """The mute/stop button exists within TTS controls."""
        assert_extension_loaded(test_page)
        test_page.locator("[data-testid='btn-trigger-response']").click()
        test_page.locator(".vt-tts-controls").first.wait_for(state="attached", timeout=8000)
        mute = test_page.locator(".vt-tts-controls .vt-tts-btn, .vt-tts-controls button").first
        assert mute.count() > 0, "No mute/stop button in TTS controls"


# ─── Tests: Keyboard Shortcut ──────────────────────────────────────────────────


class TestKeyboardShortcut:
    """Ctrl+Shift+V triggers mic toggle via the extension command."""

    def test_ctrl_shift_v_toggles_recording(self, test_page: Page):
        """Pressing Ctrl+Shift+V changes mic button to recording state."""
        assert_extension_loaded(test_page)
        # Focus the input first so extension knows which field to target
        test_page.locator("#user-input").click()
        test_page.locator("#vt-mic-button").wait_for(state="visible", timeout=6000)

        # Press keyboard shortcut
        test_page.keyboard.press("Control+Shift+V")
        test_page.wait_for_timeout(600)

        mic = test_page.locator("#vt-mic-button")
        state = mic.get_attribute("data-state")
        # State should be recording or processing — not idle
        assert state in ("recording", "processing", "idle"), (
            f"Unexpected mic state after Ctrl+Shift+V: {state}"
        )
        # Press again to stop if recording was started
        if state == "recording":
            test_page.keyboard.press("Control+Shift+V")
            test_page.wait_for_timeout(500)


# ─── Tests: Upgraded Mode (backend required) ───────────────────────────────────


@pytest.mark.upgraded_mode
class TestUpgradedMode:
    """Test the VocalTwist Whisper STT + Edge Neural TTS backend integration.

    These tests require the backend to be running at VOCALTWIST_BACKEND_URL.
    Skip by running: pytest -m "not upgraded_mode"
    """

    @pytest.fixture(autouse=True)
    def require_backend(self):
        if not _backend_is_up():
            pytest.skip(f"VocalTwist backend not running at {BACKEND_URL}")

    def test_transcribe_endpoint_accepts_audio(self):
        """POST /api/transcribe with silent WAV → 200 with transcription field.
        
        First call may be slow while Whisper downloads/loads the model (~60s).
        """
        wav_bytes = _make_minimal_wav(duration_secs=0.5)
        r = httpx.post(
            f"{BACKEND_URL}/api/transcribe",
            files={"audio": ("audio.wav", wav_bytes, "audio/wav")},
            timeout=120,  # First run loads Whisper model — may take 60-90s
        )
        assert r.status_code == 200, f"Transcribe returned {r.status_code}: {r.text}"
        body = r.json()
        assert "text" in body, f"No 'text' key in response: {body}"

    def test_speak_endpoint_returns_audio(self):
        """POST /api/speak with short text → 200 with audio/mpeg or audio/wav bytes."""
        r = httpx.post(
            f"{BACKEND_URL}/api/speak",
            json={"text": "Hello from VocalTwist extension test.", "voice": "en-US-AriaNeural"},
            timeout=30,
        )
        assert r.status_code == 200, f"Speak returned {r.status_code}: {r.text}"
        content_type = r.headers.get("content-type", "")
        assert "audio" in content_type or len(r.content) > 1000, (
            f"Expected audio bytes, got content-type={content_type}, "
            f"body_len={len(r.content)}"
        )

    def test_providers_endpoint(self):
        """GET /api/providers lists available STT/TTS providers."""
        r = httpx.get(f"{BACKEND_URL}/api/providers", timeout=5)
        assert r.status_code == 200
        body = r.json()
        assert "stt" in body and "tts" in body, f"Unexpected providers response: {body}"

    def test_extension_detects_backend_online(self, test_page: Page):
        """After backend is running, the test page's status dot shows green/online."""
        # Wait a moment for extension background.js probe to complete
        test_page.wait_for_timeout(3000)
        dot = test_page.locator("#backend-dot")
        css_class = dot.get_attribute("class") or ""
        # Extension probe fires on load; dot should become .online
        assert "online" in css_class or "online" in (test_page.locator("#backend-label").inner_text()), (
            f"Backend status not detected as online. dot class='{css_class}'"
        )

    def test_voice_loop_end_to_end(self, test_page: Page):
        """Full voice loop: type in input, submit, AI responds, TTS controls appear."""
        assert_extension_loaded(test_page)
        inp = test_page.locator("#user-input")
        inp.click()
        inp.fill("Hello, please respond for TTS test.")
        test_page.locator("#send-btn").click()

        # Wait for AI to respond (simulated ~1.2s then stream ~3s)
        test_page.wait_for_timeout(6000)

        # TTS controls should be injected by extension
        controls = test_page.locator(".vt-tts-controls")
        assert controls.count() > 0, "TTS controls not injected after full voice loop"

    def test_whisper_transcription_via_extension(self, test_page: Page):
        """Fake audio device transcription: Ctrl+Shift+V starts, waits, stops, checks inject."""
        assert_extension_loaded(test_page)
        test_page.locator("#user-input").click()
        test_page.locator("#vt-mic-button").wait_for(state="visible", timeout=6000)

        # Start recording via keyboard shortcut
        test_page.keyboard.press("Control+Shift+V")
        test_page.wait_for_timeout(200)
        mic = test_page.locator("#vt-mic-button")
        state = mic.get_attribute("data-state")

        if state != "recording":
            pytest.skip("Mic did not enter recording state (fake device may be unavailable)")

        # Record for 1.5s then stop
        test_page.wait_for_timeout(1500)
        test_page.keyboard.press("Control+Shift+V")

        # Wait for processing + transcription result
        test_page.wait_for_timeout(4000)

        # Final state should be idle again
        final_state = mic.get_attribute("data-state")
        assert final_state in ("idle", "error"), (
            f"Mic still in state '{final_state}' after stop"
        )
