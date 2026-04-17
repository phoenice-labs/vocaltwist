"""Pytest configuration for VocalTwist extension E2E tests.

Provides:
  - Console message capture for every browser page
  - Screenshot on test failure (saved to VocalTwistTest/tests/e2e/screenshots/)
  - Extension-loaded check: tests that depend on the extension are skipped
    with a clear message if the content script did not inject.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

SCREENSHOTS_DIR = Path(__file__).parent / "screenshots"
SCREENSHOTS_DIR.mkdir(exist_ok=True)

# ─── Console capture ──────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _capture_console(request):
    """Attach a console listener to every test_page, print on failure."""
    test_page = request.node.funcargs.get("test_page")
    if test_page is None:
        yield
        return

    messages: list[str] = []

    def _on_console(msg):
        messages.append(f"[{msg.type.upper()}] {msg.text}")

    test_page.on("console", _on_console)

    yield  # run the test

    if hasattr(request.node, "rep_call") and request.node.rep_call.failed:
        print(f"\n{'='*60}")
        print(f"Browser console for '{request.node.name}':")
        for m in messages:
            print(f"  {m}")
        print(f"{'='*60}")

        # Screenshot on failure
        try:
            screenshot_path = SCREENSHOTS_DIR / f"{request.node.name}.png"
            test_page.screenshot(path=str(screenshot_path))
            print(f"Screenshot saved: {screenshot_path}")
        except Exception:
            pass


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Store test outcome in item so fixtures can access it."""
    outcome = yield
    rep = outcome.get_result()
    setattr(item, "rep_" + rep.when, rep)


# ─── Extension-loaded assertion ───────────────────────────────────────────────


def assert_extension_loaded(page) -> None:
    """Raise pytest.skip if the VocalTwist extension content script did not inject.

    The content script sets `data-vt-loaded="1"` on <html> at startup.
    If it's absent after 4s, the extension is not running in this browser.
    """
    try:
        page.wait_for_selector("html[data-vt-loaded='1']", timeout=4000)
    except Exception:
        # Gather diagnostic info
        stylesheets = page.evaluate(
            "() => Array.from(document.styleSheets).map(s=>s.href||'(inline)').join(', ')"
        )
        raise pytest.skip.Exception(
            "VocalTwist extension content script did not inject "
            f"(data-vt-loaded not found after 4s). "
            f"Loaded stylesheets: {stylesheets}. "
            "Ensure the extension is loaded in Chrome with developer mode enabled, "
            "or run the tests with EXTENSION_PATH set to the correct path."
        )
