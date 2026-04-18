"""VocalTwist Allowed-Sites (Allowlist) Feature Tests.

Verifies that the extension activates ONLY on pages/origins that are
registered in chrome.storage.sync.allowedSites:

  Case 1 — Empty allowlist (default): extension works on ALL pages.
  Case 2 — Allowlist contains current page URL: extension IS active.
  Case 3 — Allowlist does NOT contain current URL: extension is INACTIVE.
  Case 4 — Allowlist contains only the origin (not full path): extension
            IS active on any page on that origin.

Prerequisites
-------------
- VocalTwist backend running at http://localhost:8000
- Extension loaded with --load-extension

Run
---
    pytest VocalTwistTest/tests/e2e/test_allowlist_e2e.py -v
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest
from playwright.sync_api import BrowserContext, Page, sync_playwright

# ─── Config ──────────────────────────────────────────────────────────────────

BACKEND_URL: str = os.getenv("VOCALTWIST_BACKEND_URL", "http://localhost:8000")
TEST_PAGE_URL: str = f"{BACKEND_URL}/test-extension.html"
OTHER_PAGE_URL: str = f"{BACKEND_URL}/"           # root — NOT the test page
EXT_PATH: str = str(Path(__file__).parents[3] / "vocaltwist-extension")

# ─── Helpers ─────────────────────────────────────────────────────────────────


def _set_allowed_sites(ctx: BrowserContext, ext_id: str, allowed: list[str]) -> None:
    """Write allowedSites into chrome.storage.sync via the popup context."""
    serialised = json.dumps(allowed)
    popup = ctx.new_page()
    try:
        popup.goto(f"chrome-extension://{ext_id}/popup/popup.html")
        popup.wait_for_selector("#saveBtn", timeout=5_000)
        popup.evaluate(
            f"""() => new Promise(resolve =>
                chrome.storage.sync.set({{ allowedSites: {serialised} }}, resolve))"""
        )
    finally:
        popup.close()
    # Give the storage.onChanged event time to fire in content scripts
    time.sleep(0.5)


def _get_allowed_sites(ctx: BrowserContext, ext_id: str) -> list[str]:
    popup = ctx.new_page()
    try:
        popup.goto(f"chrome-extension://{ext_id}/popup/popup.html")
        popup.wait_for_selector("#saveBtn", timeout=5_000)
        result = popup.evaluate(
            """() => new Promise(resolve =>
                chrome.storage.sync.get(['allowedSites'], d =>
                    resolve(d.allowedSites || [])))"""
        )
    finally:
        popup.close()
    return result


def _extension_loaded(page: Page, timeout_ms: int = 6_000) -> bool:
    """Returns True if VocalTwist fully activated on this page (data-vt-active=1)."""
    try:
        page.wait_for_selector("[data-vt-active='1']", timeout=timeout_ms)
        return True
    except Exception:
        return False


def _extension_inactive(page: Page, timeout_ms: int = 3_000) -> bool:
    """Returns True if VocalTwist did NOT activate after timeout."""
    try:
        page.wait_for_selector("[data-vt-active='1']", timeout=timeout_ms)
        return False           # activated → NOT inactive
    except Exception:
        return True            # timed out → correctly inactive


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def browser_context():
    with sync_playwright() as pw:
        user_data_dir = tempfile.mkdtemp(prefix="vt_allowlist_")
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
        # Navigate to the test page so the SW registers (same trigger as language tests)
        warmup = ctx.new_page()
        try:
            warmup.goto(TEST_PAGE_URL, timeout=15_000)
        except Exception:
            pass
        time.sleep(4)   # let the service worker start and probe backend
        warmup.close()
        yield ctx
        ctx.close()


@pytest.fixture(scope="module")
def ext_id(browser_context: BrowserContext) -> str:
    workers = browser_context.service_workers
    if not workers:
        # Retry once — SW may still be starting
        time.sleep(2)
        workers = browser_context.service_workers
    assert workers, (
        "Extension service worker not found. "
        "Ensure the extension loaded correctly."
    )
    return workers[0].url.split("/")[2]


@pytest.fixture(autouse=True)
def reset_allowlist(browser_context: BrowserContext, ext_id: str):
    """Reset allowedSites to [] before and after every test."""
    _set_allowed_sites(browser_context, ext_id, [])
    yield
    _set_allowed_sites(browser_context, ext_id, [])


@pytest.fixture
def fresh_page(browser_context: BrowserContext):
    """Open a NEW tab for each test and close it after."""
    page = browser_context.new_page()
    yield page
    page.close()


# ─── Tests ───────────────────────────────────────────────────────────────────


class TestAllowlistFeature:

    def test_empty_allowlist_works_everywhere(
        self, fresh_page: Page, browser_context: BrowserContext, ext_id: str
    ):
        """Default: allowedSites=[] — extension activates on any page."""
        stored = _get_allowed_sites(browser_context, ext_id)
        assert stored == [], f"Expected empty allowedSites, got {stored}"

        fresh_page.goto(TEST_PAGE_URL)
        assert _extension_loaded(fresh_page), (
            "Extension should be active when allowedSites is empty"
        )

    def test_allowlist_specific_page_activates(
        self, fresh_page: Page, browser_context: BrowserContext, ext_id: str
    ):
        """Allowlist = [full URL of test page] → extension IS active there."""
        _set_allowed_sites(browser_context, ext_id, [TEST_PAGE_URL])

        fresh_page.goto(TEST_PAGE_URL)
        assert _extension_loaded(fresh_page), (
            f"Extension should be active when {TEST_PAGE_URL} is in allowedSites"
        )

    def test_allowlist_blocks_unlisted_page(
        self, fresh_page: Page, browser_context: BrowserContext, ext_id: str
    ):
        """Allowlist = [specific page] → extension is INACTIVE on a different page."""
        _set_allowed_sites(browser_context, ext_id, [TEST_PAGE_URL])

        # Navigate to a DIFFERENT page on the same server
        fresh_page.goto(OTHER_PAGE_URL)
        inactive = _extension_inactive(fresh_page, timeout_ms=3_000)
        assert inactive, (
            f"Extension should be INACTIVE on {OTHER_PAGE_URL} "
            f"when allowedSites only contains {TEST_PAGE_URL}"
        )

    def test_allowlist_origin_activates_all_pages_on_site(
        self, fresh_page: Page, browser_context: BrowserContext, ext_id: str
    ):
        """Allowlist = [origin only] → works on all pages on that origin."""
        origin = BACKEND_URL.rstrip("/")   # e.g. http://localhost:8000
        _set_allowed_sites(browser_context, ext_id, [origin])

        # Test page (has a path) should still be active
        fresh_page.goto(TEST_PAGE_URL)
        assert _extension_loaded(fresh_page), (
            f"Extension should be active on {TEST_PAGE_URL} "
            f"when allowedSites contains origin {origin}"
        )

    def test_allowlist_origin_covers_other_page(
        self, fresh_page: Page, browser_context: BrowserContext, ext_id: str
    ):
        """Origin entry covers pages other than the test page on the same origin."""
        origin = BACKEND_URL.rstrip("/")
        _set_allowed_sites(browser_context, ext_id, [origin])

        # Navigate to a different path on the same origin
        fresh_page.goto(OTHER_PAGE_URL)
        assert _extension_loaded(fresh_page, timeout_ms=6_000), (
            f"Extension should be active on {OTHER_PAGE_URL} "
            f"when allowedSites contains origin {origin}"
        )