"""VocalTwist Extension Language End-to-End Tests.

Tests run through the ACTUAL Chrome extension — not via direct API calls.
For each supported language the test:

  1. Opens the extension popup page, selects the language, and saves.
  2. TTS: Injects a new AI response bubble into the test page chat area.
     The extension's response-watcher detects it (Tier-1, localhost registered
     in site-registry.json) and calls orchestrator.speak() → tts-vocaltwist.js
     POSTs to /api/speak with the correct ISO-639-1 language code.
  3. STT: Sends a TOGGLE_MIC message to the content script via the background
     service worker (equivalent to Ctrl+Shift+V).  The offscreen document
     records fake audio and sends it to the backend at /api/transcribe with the
     correct ?language= query parameter.

Both requests are captured server-side by _TestCaptureMiddleware in app.py and
exposed via /api/test/last-speak and /api/test/last-transcribe so tests can
assert on language without relying on CDP network interception (which cannot
see extension content-script requests).

Prerequisites
-------------
- VocalTwist backend running at http://localhost:8000  (uvicorn VocalTwistTest.app:app)
- Chrome / Chromium available via playwright
- Extension folder: vocaltwist-extension/ at repo root

Run
---
    pytest VocalTwistTest/tests/e2e/test_extension_language_e2e.py -v
    # headed (watch the browser):
    pytest VocalTwistTest/tests/e2e/test_extension_language_e2e.py -v --headed
"""
from __future__ import annotations

import os
import time
import tempfile
from pathlib import Path
from typing import NamedTuple

import httpx
import pytest
from playwright.sync_api import BrowserContext, Page, sync_playwright

# ─── Configuration ────────────────────────────────────────────────────────────

BACKEND_URL: str = os.getenv("VOCALTWIST_BACKEND_URL", "http://localhost:8000")
TEST_PAGE_URL: str = f"{BACKEND_URL}/test-extension.html"

# Absolute path to the unpacked extension
EXT_PATH: str = str(Path(__file__).parents[3] / "vocaltwist-extension")


# ─── Language definitions ──────────────────────────────────────────────────────

class LangCase(NamedTuple):
    code:  str   # ISO 639-1 short code (what backend expects)
    bcp47: str   # BCP-47 value stored in chrome.storage / popup select
    name:  str


LANGUAGES: list[LangCase] = [
    LangCase("en", "en-US", "English"),
    LangCase("hi", "hi-IN", "Hindi"),
    LangCase("es", "es-ES", "Spanish"),
    LangCase("fr", "fr-FR", "French"),
    LangCase("de", "de-DE", "German"),
    LangCase("zh", "zh-CN", "Chinese"),
    LangCase("ja", "ja-JP", "Japanese"),
    LangCase("pt", "pt-BR", "Portuguese"),
    LangCase("ar", "ar-SA", "Arabic"),
]

# TTS debounce is 1.5 s; add buffer for network + AudioContext decode
TTS_WAIT_S = 5.0

# STT: recording duration + Whisper processing latency
STT_RECORD_S  = 2.5   # How long to hold the fake mic open
STT_PROCESS_S = 12.0  # Max time for Whisper to respond (can be slow)


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def browser_context():
    """Persistent Chrome context with the VocalTwist extension loaded.

    Headless mode is intentionally disabled — Chrome extensions don't
    run in legacy headless mode.  Use --headless=new or headed mode.
    """
    with sync_playwright() as pw:
        user_data_dir = tempfile.mkdtemp(prefix="vt_e2e_")
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            args=[
                f"--load-extension={EXT_PATH}",
                f"--disable-extensions-except={EXT_PATH}",
                "--use-fake-ui-for-media-stream",       # auto-grant mic
                "--use-fake-device-for-media-stream",   # synthetic audio
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                # Suppress "Chrome is being controlled" infobars
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            permissions=["microphone"],
            ignore_https_errors=True,
        )
        # Give the extension service worker time to start and probe the backend
        time.sleep(4)
        yield ctx
        ctx.close()


@pytest.fixture(scope="module")
def ext_id(browser_context: BrowserContext) -> str:
    """Extract the extension ID from the background service worker URL."""
    workers = browser_context.service_workers
    assert workers, (
        "Extension service worker not found. "
        "Ensure the extension loaded correctly."
    )
    # URL format: chrome-extension://<ID>/background.js
    return workers[0].url.split("/")[2]


@pytest.fixture(scope="module")
def test_page(browser_context: BrowserContext, ext_id: str) -> Page:
    """Open the extension test page and wait for the content script to inject."""
    # Close any tabs opened by the extension during install (e.g. onboarding)
    for extra in browser_context.pages[1:]:
        try:
            extra.close()
        except Exception:
            pass

    page = browser_context.new_page()
    page.goto(TEST_PAGE_URL)

    # Wait for content script sentinel attribute
    page.wait_for_selector("[data-vt-loaded='1']", timeout=15_000)
    time.sleep(1)  # Allow orchestrator + response-watcher init to settle
    return page


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _set_language(ctx: BrowserContext, ext_id: str, bcp47: str) -> None:
    """Open the extension popup, select *bcp47*, save, and close."""
    popup = ctx.new_page()
    try:
        popup.goto(f"chrome-extension://{ext_id}/popup/popup.html")
        popup.wait_for_selector("#languageSelect", timeout=5_000)
        popup.select_option("#languageSelect", bcp47)
        popup.click("#saveBtn")
        # Wait until the button confirms the save
        popup.wait_for_function(
            "document.getElementById('saveBtn').textContent.includes('Saved')",
            timeout=5_000,
        )
    finally:
        popup.close()
    # Allow SETTINGS_UPDATED broadcast to reach the test-page content script
    time.sleep(0.8)


def _inject_ai_response(page: Page, text: str) -> None:
    """Append a *new* .message.assistant .bubble into #chat-messages.

    The response-watcher (Tier-1, localhost in site-registry.json) will detect
    the new node and trigger TTS after its 1.5 s debounce.
    """
    page.evaluate(
        """(text) => {
            const msgs = document.getElementById('chat-messages');
            const wrap   = document.createElement('div');
            wrap.className = 'message assistant';
            const avatar = document.createElement('div');
            avatar.className = 'avatar';
            avatar.textContent = '🤖';
            const bubble = document.createElement('div');
            bubble.className = 'bubble';
            bubble.textContent = text;
            wrap.appendChild(avatar);
            wrap.appendChild(bubble);
            msgs.appendChild(wrap);
            msgs.scrollTop = msgs.scrollHeight;
        }""",
        text,
    )


def _toggle_mic(ctx: BrowserContext, page: Page) -> None:
    """Send TOGGLE_MIC to the test page content script via the service worker.

    This is equivalent to pressing Ctrl+Shift+V.  Keyboard shortcuts
    registered via chrome.commands are handled by the browser, not the
    renderer, so page.keyboard.press() cannot trigger them.
    """
    workers = ctx.service_workers
    assert workers, "Background service worker not running"
    tab_url = page.url
    workers[0].evaluate(
        """async (url) => {
            const tabs = await chrome.tabs.query({ url });
            if (tabs.length > 0) {
                chrome.tabs.sendMessage(tabs[0].id, { type: 'TOGGLE_MIC' });
            }
        }""",
        tab_url,
    )


def _reset_captures() -> None:
    """Clear server-side test capture state."""
    httpx.post(f"{BACKEND_URL}/api/test/reset", timeout=5)


def _last_speak() -> dict:
    r = httpx.get(f"{BACKEND_URL}/api/test/last-speak", timeout=5)
    return r.json() if r.status_code == 200 else {}


def _last_transcribe() -> dict:
    r = httpx.get(f"{BACKEND_URL}/api/test/last-transcribe", timeout=5)
    return r.json() if r.status_code == 200 else {}


# ─── TTS Tests ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("lang", LANGUAGES, ids=[l.bcp47 for l in LANGUAGES])
def test_tts_language(
    browser_context: BrowserContext,
    ext_id: str,
    test_page: Page,
    lang: LangCase,
) -> None:
    """Extension sends correct ISO-639-1 language code to /api/speak."""
    _reset_captures()
    _set_language(browser_context, ext_id, lang.bcp47)

    # Unique text per language so response-watcher's _lastText guard doesn't block
    test_text = (
        f"[{lang.bcp47}] VocalTwist language verification test for {lang.name}. "
        "This response is automatically injected to confirm that the extension "
        "correctly routes the language setting through to the text to speech backend."
    )
    _inject_ai_response(test_page, test_text)

    # Wait: TTS_DEBOUNCE_MS (1.5 s) + network + AudioContext decode
    time.sleep(TTS_WAIT_S)

    data = _last_speak()
    assert data, (
        f"[{lang.bcp47}] No /api/speak request captured. "
        "TTS may be disabled, backend offline, or response-watcher not triggered."
    )
    got = data.get("language")
    assert got == lang.code, (
        f"[{lang.bcp47}] Expected language='{lang.code}' in /api/speak body, "
        f"got '{got}'. Check tts-vocaltwist.js BCP-47 normalization."
    )


# ─── STT Tests ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("lang", LANGUAGES, ids=[l.bcp47 for l in LANGUAGES])
def test_stt_language(
    browser_context: BrowserContext,
    ext_id: str,
    test_page: Page,
    lang: LangCase,
) -> None:
    """Extension sends correct ISO-639-1 language code to /api/transcribe."""
    _reset_captures()
    _set_language(browser_context, ext_id, lang.bcp47)

    # Focus the textarea so the content script knows where to inject transcripts
    test_page.click("#user-input")
    time.sleep(0.3)

    # Start recording (equivalent to Ctrl+Shift+V)
    _toggle_mic(browser_context, test_page)
    time.sleep(STT_RECORD_S)

    # Stop recording (second TOGGLE_MIC)
    _toggle_mic(browser_context, test_page)

    # Wait for offscreen to process audio, background to relay, orchestrator to
    # call sttProvider.transcribe(), and Whisper to respond
    deadline = time.monotonic() + STT_PROCESS_S
    data: dict = {}
    while time.monotonic() < deadline:
        data = _last_transcribe()
        if data:
            break
        time.sleep(1)

    assert data, (
        f"[{lang.bcp47}] No /api/transcribe request captured within "
        f"{STT_PROCESS_S:.0f} s. "
        "Check that the backend is online, fake-device audio is enabled, "
        "and the extension is in push-to-talk mode."
    )
    got = data.get("language")
    assert got == lang.code, (
        f"[{lang.bcp47}] Expected ?language={lang.code} in /api/transcribe URL, "
        f"got '{got}'. Check stt-vocaltwist.js BCP-47 normalization."
    )

    # Brief pause between STT tests to respect /api/transcribe rate limit (20/min)
    time.sleep(3)
