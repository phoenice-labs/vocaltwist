"""VocalTwist Extension Offline (No-Backend) End-to-End Tests.

When the VocalTwist backend is unavailable, the extension MUST fall back to:
  - STT: browser Web Speech API (webkitSpeechRecognition)
  - TTS: browser speechSynthesis

These tests verify that native fallback is used for all 9 supported languages
by using the test server's /api/test/set-offline endpoint to make /api/health
return 503.  The extension's direct-probe in content.js sees 503 and sets
backendOnline=false — no port-change or race-condition involved.

DOM attributes written by voice-orchestrator.js are observed:

  data-vt-last-speak-provider  — 'native' | 'vocaltwist'
  data-vt-last-speak-lang      — BCP-47 language stored in settings
  data-vt-last-stt-provider    — 'native' | 'vocaltwist'
  data-vt-last-stt-language    — BCP-47 language stored in settings

Run
---
    pytest VocalTwistTest/tests/e2e/test_extension_offline_e2e.py -v
    pytest VocalTwistTest/tests/e2e/test_extension_offline_e2e.py -v --headed
"""
from __future__ import annotations

import os
import time
import tempfile
import requests as _requests
from pathlib import Path
from typing import NamedTuple

import pytest
from playwright.sync_api import BrowserContext, Page, sync_playwright

# ─── Configuration ────────────────────────────────────────────────────────────

# The test server serves the test page HTML.  The extension's backendUrl stays
# at localhost:8000; the server's /api/test/set-offline endpoint makes
# /api/health return 503 so the extension sees it as offline.
TEST_PAGE_HOST: str = os.getenv("VOCALTWIST_BACKEND_URL", "http://localhost:8000")
TEST_PAGE_URL: str  = f"{TEST_PAGE_HOST}/test-extension.html"
SET_OFFLINE_URL: str = f"{TEST_PAGE_HOST}/api/test/set-offline"

# Absolute path to the unpacked extension
EXT_PATH: str = str(Path(__file__).parents[3] / "vocaltwist-extension")


# ─── Language definitions ──────────────────────────────────────────────────────

class LangCase(NamedTuple):
    code:  str   # ISO 639-1 short code
    bcp47: str   # BCP-47 value stored in chrome.storage
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

# TTS debounce 1.5 s + speechSynthesis latency
TTS_WAIT_S = 4.0


# ─── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def offline_browser_context():
    """Persistent Chrome context with the extension loading against localhost:8000.
    The server's /api/health is toggled to 503 so the extension sees no backend."""
    with sync_playwright() as pw:
        user_data_dir = tempfile.mkdtemp(prefix="vt_offline_")
        ctx = pw.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            args=[
                f"--load-extension={EXT_PATH}",
                f"--disable-extensions-except={EXT_PATH}",
                "--use-fake-ui-for-media-stream",
                "--use-fake-device-for-media-stream",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
                "--disable-infobars",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            permissions=["microphone"],
            ignore_https_errors=True,
        )
        # Give the extension service worker time to start
        time.sleep(3)
        yield ctx
        # Restore server to online mode before closing
        try:
            _requests.get(f"{SET_OFFLINE_URL}?offline=false", timeout=3)
        except Exception:
            pass
        ctx.close()


@pytest.fixture(scope="module")
def offline_ext_id(offline_browser_context: BrowserContext) -> str:
    workers = offline_browser_context.service_workers
    assert workers, "Extension service worker not found"
    return workers[0].url.split("/")[2]


@pytest.fixture(scope="module")
def offline_test_page(offline_browser_context: BrowserContext, offline_ext_id: str) -> Page:
    """Open the test page with the server reporting health=503 (offline simulation)."""

    # --- Step 1: Reset extension settings to default (backendUrl = localhost:8000) ---
    setup = offline_browser_context.new_page()
    try:
        setup.goto(
            f"chrome-extension://{offline_ext_id}/popup/popup.html",
            wait_until="domcontentloaded",
        )
        setup.wait_for_selector("#saveBtn", timeout=8_000)
        # Ensure the backendUrl points to the real server (not any leftover port-9999 value)
        setup.evaluate(
            f"""new Promise(resolve => {{
                chrome.storage.sync.set({{
                    backendUrl:    '{TEST_PAGE_HOST}',
                    enabled:       true,
                    ttsEnabled:    true,
                    showMicButton: true,
                    language:      'en-US',
                }}, resolve);
            }})"""
        )
    finally:
        setup.close()

    # --- Step 2: Tell the server to simulate offline (health returns 503) ---
    resp = _requests.get(f"{SET_OFFLINE_URL}?offline=true", timeout=5)
    assert resp.status_code == 200, f"set-offline failed: {resp.text}"

    # Close any extra tabs (onboarding etc.)
    for extra in offline_browser_context.pages[1:]:
        try:
            extra.close()
        except Exception:
            pass

    # --- Step 3: Open test page (content.js probes /api/health → 503 → offline) ---
    page = offline_browser_context.new_page()
    page.goto(TEST_PAGE_URL)
    page.wait_for_selector("[data-vt-loaded='1']", timeout=15_000)

    # Wait for content.js checkBackendDirect to resolve and write vtBackendOnline
    page.wait_for_function(
        "document.documentElement.dataset.vtBackendOnline === 'false'",
        timeout=10_000,
    )

    return page


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _set_language_offline(
    ctx: BrowserContext,
    ext_id: str,
    bcp47: str,
    page: Page,
) -> None:
    """Set language via direct chrome.storage.sync.set.

    background.js's storage.onChanged listener handles the backendUrl key;
    content.js's storage.onChanged listener updates vtLanguage for other keys.
    """
    popup = ctx.new_page()
    try:
        popup.goto(
            f"chrome-extension://{ext_id}/popup/popup.html",
            wait_until="domcontentloaded",
        )
        popup.wait_for_selector("#saveBtn", timeout=5_000)
        popup.evaluate(
            f"""new Promise(resolve => {{
                chrome.storage.sync.set({{
                    language:   '{bcp47}',
                    backendUrl: '{TEST_PAGE_HOST}',
                    ttsEnabled: true,
                }}, resolve);
            }})"""
        )
        time.sleep(0.3)
    finally:
        popup.close()

    # Wait for content.js chrome.storage.onChanged to propagate vtLanguage
    try:
        page.wait_for_function(
            f"document.documentElement.dataset.vtLanguage === '{bcp47}'",
            timeout=6_000,
        )
    except Exception:
        actual = page.evaluate(
            "document.documentElement.dataset.vtLanguage || 'not-set'"
        )
        raise RuntimeError(
            f"data-vt-language never updated to '{bcp47}' (got '{actual}')."
        )
    time.sleep(0.3)


def _inject_ai_response(page: Page, text: str) -> None:
    """Inject a new .message.assistant .bubble so the response-watcher triggers TTS."""
    page.evaluate(
        """(text) => {
            const msgs   = document.getElementById('chat-messages');
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
    """Send TOGGLE_MIC to the content script via the service worker."""
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


def _reset_dom_attrs(page: Page) -> None:
    """Remove last-speak / last-stt DOM attrs so each test starts clean."""
    page.evaluate("""() => {
        const d = document.documentElement.dataset;
        delete d.vtLastSpeakProvider;
        delete d.vtLastSpeakLang;
        delete d.vtLastSpeakTs;
        delete d.vtLastSttProvider;
        delete d.vtLastSttLanguage;
        delete d.vtLastSttTs;
    }""")


# ─── TTS Offline Tests ────────────────────────────────────────────────────────


@pytest.mark.parametrize("lang", LANGUAGES, ids=[l.bcp47 for l in LANGUAGES])
def test_tts_offline_native(
    offline_browser_context: BrowserContext,
    offline_ext_id: str,
    offline_test_page: Page,
    lang: LangCase,
) -> None:
    """Without backend: TTS must use native speechSynthesis with the correct language."""
    _reset_dom_attrs(offline_test_page)
    _set_language_offline(offline_browser_context, offline_ext_id, lang.bcp47, offline_test_page)

    test_text = f"[offline-tts-{lang.bcp47}] Testing native TTS fallback for {lang.name}."
    _inject_ai_response(offline_test_page, test_text)

    # Wait for the response-watcher debounce (1.5 s) + orchestrator speak() call
    time.sleep(TTS_WAIT_S)

    provider = offline_test_page.evaluate(
        "document.documentElement.dataset.vtLastSpeakProvider || 'not-called'"
    )
    speak_lang = offline_test_page.evaluate(
        "document.documentElement.dataset.vtLastSpeakLang || 'not-set'"
    )

    assert provider == "native", (
        f"[{lang.name}] Expected TTS provider='native' (offline) but got '{provider}'. "
        f"vtBackendOnline={offline_test_page.evaluate('document.documentElement.dataset.vtBackendOnline')}"
    )
    assert speak_lang == lang.bcp47, (
        f"[{lang.name}] Expected TTS language='{lang.bcp47}' but got '{speak_lang}'"
    )


# ─── STT Offline Tests ────────────────────────────────────────────────────────


@pytest.mark.parametrize("lang", LANGUAGES, ids=[l.bcp47 for l in LANGUAGES])
def test_stt_offline_native(
    offline_browser_context: BrowserContext,
    offline_ext_id: str,
    offline_test_page: Page,
    lang: LangCase,
) -> None:
    """Without backend: STT must use native Web Speech API with the correct BCP-47 language.

    In headless Chrome with --use-fake-device-for-media-stream, webkitSpeechRecognition
    may error out immediately (no-speech / audio-capture).  That is acceptable — we only
    verify that startRecording() chose the native provider and set the language attr
    *before* the recognition attempt, not that transcription succeeded.
    """
    _reset_dom_attrs(offline_test_page)
    _set_language_offline(offline_browser_context, offline_ext_id, lang.bcp47, offline_test_page)

    # Pre-flight: ensure the mic is idle before toggling (previous test may have
    # left the orchestrator in recording/processing state).
    mic_before = offline_test_page.evaluate(
        "window.__vtOrchestrator?.isRecording ? 'recording' : 'idle'"
    )
    if mic_before == "recording":
        _toggle_mic(offline_browser_context, offline_test_page)
        time.sleep(1.5)  # wait for stop + safety timeout

    # Trigger mic toggle — content script startRecording() will set DOM attrs immediately
    _reset_dom_attrs(offline_test_page)
    _toggle_mic(offline_browser_context, offline_test_page)
    time.sleep(0.4)  # Small pause for async SW→content message delivery

    # Wait for voice-orchestrator.js to write the STT provider attrs
    try:
        offline_test_page.wait_for_function(
            "document.documentElement.dataset.vtLastSttProvider !== undefined",
            timeout=8_000,
        )
    except Exception:
        pass  # Attr not written yet — will be caught by the assert below

    provider = offline_test_page.evaluate(
        "document.documentElement.dataset.vtLastSttProvider || 'not-called'"
    )
    stt_lang = offline_test_page.evaluate(
        "document.documentElement.dataset.vtLastSttLanguage || 'not-set'"
    )

    # Stop the mic if it is still in recording state (cleanup for next test)
    mic_state = offline_test_page.evaluate(
        "window.__vtOrchestrator?.isRecording ? 'recording' : 'idle'"
    )
    if mic_state == "recording":
        _toggle_mic(offline_browser_context, offline_test_page)
        time.sleep(1.0)

    assert provider == "native", (
        f"[{lang.name}] Expected STT provider='native' (offline) but got '{provider}'. "
        f"vtBackendOnline={offline_test_page.evaluate('document.documentElement.dataset.vtBackendOnline')}"
    )
    assert stt_lang == lang.bcp47, (
        f"[{lang.name}] Expected STT language='{lang.bcp47}' but got '{stt_lang}'"
    )
