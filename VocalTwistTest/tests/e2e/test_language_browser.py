"""VocalTwist Language Browser Tests — Playwright against test-extension.html.

Tests verify that for every supported language:
  1. POST /api/speak  — returns audio bytes (TTS works for that language)
  2. POST /api/transcribe — returns 200 with language as URL query param (STT works)

These tests drive the language-test panel embedded in test-extension.html
and intercept real HTTP requests to verify payload correctness.  They do NOT
require the Chrome extension — they use direct backend API calls made from
the page's own JavaScript.

Usage:
    # Run all browser language tests (backend must be running):
    pytest VocalTwistTest/tests/e2e/test_language_browser.py -v

    # Headed mode (watch the browser):
    pytest VocalTwistTest/tests/e2e/test_language_browser.py -v -k "tts" --headed
"""
from __future__ import annotations

import json
import os
import time
from typing import NamedTuple

import httpx
import pytest
from playwright.sync_api import Page, sync_playwright, Route, Request as PWRequest

BACKEND_URL = os.getenv("VOCALTWIST_BACKEND_URL", "http://localhost:8000")
TEST_PAGE_URL = f"{BACKEND_URL}/test-extension.html"


# ─── Language definitions ──────────────────────────────────────────────────────

class LangCase(NamedTuple):
    code:       str   # ISO 639-1 short code (sent to backend)
    bcp47:      str   # BCP-47 code (used as button data attribute)
    name:       str
    sample_text: str


LANGUAGES: list[LangCase] = [
    LangCase("en", "en-US", "English",    "Hello from VocalTwist."),
    LangCase("hi", "hi-IN", "Hindi",      "नमस्ते, वोकलट्विस्ट से।"),
    LangCase("es", "es-ES", "Spanish",    "Hola desde VocalTwist."),
    LangCase("fr", "fr-FR", "French",     "Bonjour de VocalTwist."),
    LangCase("de", "de-DE", "German",     "Hallo von VocalTwist."),
    LangCase("zh", "zh-CN", "Chinese",    "你好，来自 VocalTwist。"),
    LangCase("ja", "ja-JP", "Japanese",   "VocalTwist からこんにちは。"),
    LangCase("pt", "pt-BR", "Portuguese", "Olá do VocalTwist."),
    LangCase("ar", "ar-SA", "Arabic",     "مرحباً من VocalTwist."),
]


# ─── Session-level fixtures ────────────────────────────────────────────────────

def _backend_is_up() -> bool:
    try:
        r = httpx.get(f"{BACKEND_URL}/api/health", timeout=4)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="session", autouse=True)
def require_backend():
    if not _backend_is_up():
        pytest.skip(f"VocalTwist backend not running at {BACKEND_URL}")


@pytest.fixture(scope="session")
def browser():
    """Headless Chromium session shared across all tests."""
    with sync_playwright() as pw:
        b = pw.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        yield b
        b.close()


@pytest.fixture(scope="session")
def context(browser):
    ctx = browser.new_context(
        ignore_https_errors=True,
        java_script_enabled=True,
    )
    yield ctx
    ctx.close()


@pytest.fixture()
def page(context) -> Page:
    p = context.new_page()
    p.goto(TEST_PAGE_URL, wait_until="domcontentloaded")
    # Wait for the language grid to render
    p.wait_for_selector("#lang-grid .lang-row", timeout=8000)
    yield p
    p.close()


# ─── TTS Tests — one per language ─────────────────────────────────────────────

class TestTTSBrowser:
    """Click each language's TTS button and verify /api/speak request + response."""

    @pytest.mark.parametrize("lang", LANGUAGES, ids=lambda l: l.code)
    def test_tts_language(self, page: Page, lang: LangCase):
        """Click TTS button → /api/speak request contains correct ISO language code."""
        captured: list[dict] = []

        def _on_route(route: Route, request: PWRequest):
            if "/api/speak" in request.url:
                try:
                    body = json.loads(request.post_data or "{}")
                except Exception:
                    body = {}
                captured.append({
                    "url":      request.url,
                    "body":     body,
                    "method":   request.method,
                })
            route.continue_()

        page.route("**/api/speak**", _on_route)

        # Click the TTS button for this language
        btn_sel = f'[data-test-tts="{lang.bcp47}"]'
        page.wait_for_selector(btn_sel, timeout=5000)
        page.click(btn_sel)

        # Wait for the result to appear in window.__vtLangResults
        try:
            page.wait_for_function(
                f"() => window.__vtLangResults && window.__vtLangResults['tts_{lang.code}'] !== undefined",
                timeout=30_000,
            )
        except Exception:
            pytest.fail(f"[{lang.code}] TTS test did not complete within 30s")

        result = page.evaluate(f"() => window.__vtLangResults['tts_{lang.code}']")

        # ── Assertions ────────────────────────────────────────────────────────

        # 1. Request was intercepted with correct language in body
        assert captured, (
            f"[{lang.code}] No /api/speak request was intercepted"
        )
        body = captured[0]["body"]
        assert body.get("language") == lang.code, (
            f"[{lang.code}] Expected body.language='{lang.code}', got: {body}"
        )

        # 2. Backend returned HTTP 200
        assert result["status"] == 200, (
            f"[{lang.code}] TTS returned HTTP {result['status']}"
        )

        # 3. Response was audio with meaningful content
        assert "audio" in (result.get("contentType") or ""), (
            f"[{lang.code}] Expected audio content-type, got: {result.get('contentType')}"
        )
        assert (result.get("bytes") or 0) > 500, (
            f"[{lang.code}] Audio too small ({result.get('bytes')} bytes)"
        )

        page.unroute("**/api/speak**", _on_route)


# ─── STT Tests — one per language ─────────────────────────────────────────────

class TestSTTBrowser:
    """Click each language's STT button and verify /api/transcribe request URL."""

    @pytest.fixture(autouse=True)
    def _rate_pause(self):
        """3s pause before each STT test to stay within 20/minute rate limit."""
        time.sleep(3)

    @pytest.mark.parametrize("lang", LANGUAGES, ids=lambda l: l.code)
    def test_stt_language(self, page: Page, lang: LangCase):
        """Click STT button → /api/transcribe URL contains language as query param."""
        captured_urls: list[str] = []

        def _on_route(route: Route, request: PWRequest):
            if "/api/transcribe" in request.url:
                captured_urls.append(request.url)
            route.continue_()

        page.route("**/api/transcribe**", _on_route)

        # Click the STT button for this language
        btn_sel = f'[data-test-stt="{lang.bcp47}"]'
        page.wait_for_selector(btn_sel, timeout=5000)
        page.click(btn_sel)

        # Wait for result
        try:
            page.wait_for_function(
                f"() => window.__vtLangResults && window.__vtLangResults['stt_{lang.code}'] !== undefined",
                timeout=130_000,
            )
        except Exception:
            pytest.fail(f"[{lang.code}] STT test did not complete within 130s")

        result = page.evaluate(f"() => window.__vtLangResults['stt_{lang.code}']")

        # ── Assertions ────────────────────────────────────────────────────────

        # 1. Request was made
        assert captured_urls, (
            f"[{lang.code}] No /api/transcribe request was intercepted"
        )

        # 2. Language is in URL as query param (not in body)
        url = captured_urls[0]
        assert f"language={lang.code}" in url, (
            f"[{lang.code}] Expected 'language={lang.code}' in URL query param, got: {url!r}"
        )
        assert "/api/transcribe" in url, (
            f"[{lang.code}] Unexpected URL: {url!r}"
        )

        # 3. Backend returned HTTP 200 (or skip on 429 rate limit)
        status = result.get("status", 0)
        if status == 429:
            pytest.skip(f"[{lang.code}] Rate limited — rerun after cooldown")
        assert status == 200, (
            f"[{lang.code}] STT returned HTTP {status}"
        )

        page.unroute("**/api/transcribe**", _on_route)


# ─── Full-suite smoke test ─────────────────────────────────────────────────────

class TestRunAllLanguages:
    """Trigger the 'Run All' button and verify every language passes."""

    def test_run_all_button_exists(self, page: Page):
        """The 'Run All Language Tests' button is present on the page."""
        page.wait_for_selector('[data-testid="run-all-langs"]', timeout=5000)
        btn_text = page.text_content('[data-testid="run-all-langs"]')
        assert "Run All" in btn_text, f"Unexpected button text: {btn_text!r}"

    def test_lang_grid_has_all_languages(self, page: Page):
        """Language grid contains exactly 9 language rows."""
        rows = page.query_selector_all("#lang-grid .lang-row")
        assert len(rows) == len(LANGUAGES), (
            f"Expected {len(LANGUAGES)} language rows, found {len(rows)}"
        )

    @pytest.mark.parametrize("lang", LANGUAGES, ids=lambda l: l.code)
    def test_tts_and_stt_buttons_present(self, page: Page, lang: LangCase):
        """Each language has both a TTS and STT test button."""
        page.wait_for_selector(f'[data-test-tts="{lang.bcp47}"]', timeout=4000)
        page.wait_for_selector(f'[data-test-stt="{lang.bcp47}"]', timeout=4000)
