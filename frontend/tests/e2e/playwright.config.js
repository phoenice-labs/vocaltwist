// @ts-check
const { defineConfig, devices } = require('@playwright/test');
const path = require('path');

/**
 * Playwright configuration for VocalTwist frontend E2E tests.
 *
 * Tests run in Chromium only by default.  Add Firefox and WebKit entries
 * to `projects` for cross-browser coverage.
 *
 * All browser API calls (getUserMedia, MediaRecorder, AudioContext, Audio)
 * are mocked via page.addInitScript() inside each spec — no real microphone
 * or audio hardware is required.
 *
 * All backend API calls (/api/transcribe, /api/speak, etc.) are intercepted
 * with page.route() — no running server is required.
 */
module.exports = defineConfig({
  testDir: '.',
  testMatch: '**/*.e2e.spec.js',

  // Run tests in parallel files; serial within a file.
  fullyParallel: false,
  workers: 1,

  // Retry flaky tests once in CI.
  retries: process.env.CI ? 1 : 0,

  // Reporter: list in development, GitHub annotations in CI.
  reporter: process.env.CI ? 'github' : 'list',

  use: {
    // Base URL not needed — tests load content directly via page.setContent().
    headless: true,

    // Capture a screenshot on failure for debugging.
    screenshot: 'only-on-failure',

    // Capture a trace on first retry.
    trace: 'on-first-retry',

    // Generous timeout for async UI operations.
    actionTimeout: 10_000,
  },

  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],

  // Global timeout per test.
  timeout: 30_000,
});
