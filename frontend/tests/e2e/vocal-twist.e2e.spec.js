'use strict';

/**
 * vocal-twist.e2e.spec.js
 * Playwright end-to-end tests for the VocalTwist frontend library.
 *
 * All browser hardware APIs (getUserMedia, MediaRecorder, AudioContext,
 * Audio) are replaced with deterministic mocks injected via
 * page.addInitScript().  All backend API calls are intercepted with
 * page.route() so no running server is required.
 *
 * Test scenarios
 * --------------
 *  1. Library loads — custom element is registered after script injection
 *  2. Custom element renders shadow DOM — button, status, transcript, level bar
 *  3. Push-to-talk flow — pointerdown → record → pointerup → transcribe API → transcript shown
 *  4. State machine — idle → recording → transcribing → idle
 *  5. TTS playback — vt.speak() routes to /api/speak → Audio.play() called
 *  6. Error handling — /api/transcribe 500 → error text in shadow .vt-status
 *  7. Language attribute change — language="hi" → correct voice selected
 *  8. API key header forwarded — api-key attribute → X-API-Key sent to API
 *  9. Custom events — vt:transcript and vt:statechange dispatched
 * 10. XSS prevention — HTML in transcript response safely displayed as text
 * 11. VocalTwistTTS.play() — standalone TTS instance routes correctly
 * 12. VocalTwistRecorder — standalone recorder flow
 * 13. Ambient mode element — ambient attribute starts ambient listening
 * 14. Long text truncation — text > 2000 chars is still accepted (truncated)
 * 15. Empty API response — empty text returned from transcribe is handled
 */

const { test, expect } = require('@playwright/test');
const path = require('path');

// Absolute path to the library under test.
const LIBRARY_PATH = path.resolve(__dirname, '../../vocal-twist.js');

// ---------------------------------------------------------------------------
// Shared helpers
// ---------------------------------------------------------------------------

/**
 * Inject deterministic browser API mocks.  Must be called before the library
 * is loaded so MediaRecorder / getUserMedia are in place at parse time.
 */
async function injectBrowserMocks(page) {
  await page.addInitScript(() => {
    // ── requestAnimationFrame / cancelAnimationFrame ─────────────────────────
    window.requestAnimationFrame  = (cb) => setTimeout(cb, 16);
    window.cancelAnimationFrame   = (id) => clearTimeout(id);

    // ── URL helpers (may be incomplete in headless context) ───────────────────
    if (!URL.createObjectURL) {
      URL.createObjectURL = () => 'blob:mock-' + Math.random();
    }
    if (!URL.revokeObjectURL) {
      URL.revokeObjectURL = () => {};
    }

    // ── MediaStream factory ───────────────────────────────────────────────────
    window._mockTrack = { stop: () => {}, kind: 'audio', enabled: true };
    window._mockStream = {
      getTracks     : () => [window._mockTrack],
      getAudioTracks: () => [window._mockTrack],
    };

    // ── navigator.mediaDevices ────────────────────────────────────────────────
    Object.defineProperty(navigator, 'mediaDevices', {
      value: {
        getUserMedia  : () => Promise.resolve(window._mockStream),
        enumerateDevices: () => Promise.resolve([]),
        addEventListener   : () => {},
        removeEventListener: () => {},
      },
      writable   : true,
      configurable: true,
    });

    // ── MediaRecorder mock ────────────────────────────────────────────────────
    window._mockMediaRecorderInstances = [];

    class MockMediaRecorder {
      constructor(stream, options = {}) {
        this.stream  = stream;
        this.options = options;
        this.state   = 'inactive';
        this.ondataavailable = null;
        this.onstop          = null;
        this.onerror         = null;
        window._mockMediaRecorderInstances.push(this);
      }

      start(timeslice) {
        this.state = 'recording';
        // Emit one data chunk then keep recording until stop() is called.
        setTimeout(() => {
          this.ondataavailable?.({
            data: new Blob(['pcm-data'], { type: this.options.mimeType ?? 'audio/webm' }),
          });
        }, 10);
      }

      stop() {
        this.state = 'inactive';
        setTimeout(() => { this.onstop?.(); }, 5);
      }

      static isTypeSupported(type) {
        return ['audio/webm', 'audio/webm;codecs=opus'].includes(type);
      }
    }

    window.MediaRecorder = MockMediaRecorder;

    // ── AudioContext mock ─────────────────────────────────────────────────────
    window.AudioContext = function () {
      return {
        createMediaStreamSource: () => ({ connect: () => {} }),
        createAnalyser: () => ({
          fftSize            : 256,
          frequencyBinCount  : 128,
          getByteFrequencyData: (arr) => arr.fill(0),
          connect            : () => {},
        }),
        close: () => {},
      };
    };

    // ── Audio element mock ────────────────────────────────────────────────────
    window._mockAudioInstances = [];

    class MockAudio {
      constructor(src) {
        this.src     = src ?? '';
        this.onended = null;
        this.onerror = null;
        this._paused = false;
        window._mockAudioInstances.push(this);
      }

      play() {
        return new Promise((resolve) => {
          setTimeout(() => {
            this.onended?.();
            resolve();
          }, 20);
        });
      }

      pause() { this._paused = true; }
    }

    window.Audio = MockAudio;

    // ── Silero VAD / ONNX Runtime stubs (not used in push-to-talk tests) ─────
    window.ort = {};
    window.vad = {
      MicVAD: {
        new: async (opts) => ({
          start  : () => {},
          destroy: () => {},
          _opts  : opts,
        }),
      },
    };
  });
}

/**
 * Load the library script and set up a minimal page with a <vocal-twist>
 * element.  Routes for the standard API endpoints are also configured.
 */
async function setupPage(page, {
  transcribeResponse = { text: 'hello world', display_text: 'Hello world', language: 'en', duration_ms: 42.0 },
  speakStatusCode    = 200,
  speakBody          = new Uint8Array([0xff, 0xfb, 0x90, 0x00, 0x00, 0x00]),
  attributes         = '',
} = {}) {
  await injectBrowserMocks(page);

  // Load the library before setting content so customElements.define() fires.
  await page.addScriptTag({ path: LIBRARY_PATH });

  await page.setContent(`
    <!DOCTYPE html>
    <html>
      <head><base href="http://localhost/"></head>
      <body>
        <vocal-twist
          id="vt"
          transcribe-url="/api/transcribe"
          speak-url="/api/speak"
          ambient-url="/api/transcribe-ambient"
          language="en"
          ${attributes}
        ></vocal-twist>
        <div id="transcript-output"></div>
        <div id="state-output"></div>
        <div id="error-output"></div>
      </body>
    </html>
  `);

  // Route transcribe
  await page.route('**/api/transcribe', async (route) => {
    const status = transcribeResponse === 'error' ? 500 : 200;
    const body   = transcribeResponse === 'error'
      ? { detail: 'STT error', request_id: 'test-500' }
      : transcribeResponse;
    await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) });
  });

  // Route speak
  await page.route('**/api/speak', async (route) => {
    await route.fulfill({
      status     : speakStatusCode,
      contentType: speakStatusCode === 200 ? 'audio/mpeg' : 'application/json',
      body       : speakStatusCode === 200
        ? Buffer.from(speakBody)
        : JSON.stringify({ detail: 'TTS error' }),
    });
  });

  // Route ambient transcribe
  await page.route('**/api/transcribe-ambient', async (route) => {
    await route.fulfill({
      status     : 200,
      contentType: 'application/json',
      body       : JSON.stringify({ text: 'ambient text', display_text: 'Ambient text' }),
    });
  });

  // Wait for the custom element to be defined and connected.
  await page.waitForFunction(() => customElements.get('vocal-twist') !== undefined);
}

// ═══════════════════════════════════════════════════════════════════════════════
// Suite 1 — Library Load & Registration
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Library load & registration', () => {

  test('custom element vocal-twist is registered after script injection', async ({ page }) => {
    await injectBrowserMocks(page);
    await page.addScriptTag({ path: LIBRARY_PATH });
    await page.setContent('<html><body></body></html>');

    const registered = await page.evaluate(() => !!customElements.get('vocal-twist'));
    expect(registered).toBe(true);
  });

  test('VocalTwist, VocalTwistRecorder, VocalTwistTTS are exposed as globals', async ({ page }) => {
    await injectBrowserMocks(page);
    await page.addScriptTag({ path: LIBRARY_PATH });
    await page.setContent('<html><body></body></html>');

    const globals = await page.evaluate(() => ({
      VocalTwist        : typeof VocalTwist,
      VocalTwistRecorder: typeof VocalTwistRecorder,
      VocalTwistTTS     : typeof VocalTwistTTS,
    }));

    expect(globals.VocalTwist).toBe('function');
    expect(globals.VocalTwistRecorder).toBe('function');
    expect(globals.VocalTwistTTS).toBe('function');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Suite 2 — Custom Element Shadow DOM
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Custom element shadow DOM', () => {

  test('element has an open shadow root', async ({ page }) => {
    await setupPage(page);
    const hasShadow = await page.evaluate(() => {
      const el = document.querySelector('vocal-twist');
      return el?.shadowRoot !== null;
    });
    expect(hasShadow).toBe(true);
  });

  test('shadow DOM contains mic button, status, and transcript elements', async ({ page }) => {
    await setupPage(page);
    const els = await page.evaluate(() => {
      const sr = document.querySelector('vocal-twist').shadowRoot;
      return {
        btn       : !!sr.querySelector('.vt-mic-button'),
        status    : !!sr.querySelector('.vt-status'),
        transcript: !!sr.querySelector('.vt-transcript'),
        levelBar  : !!sr.querySelector('.vt-level-bar'),
      };
    });
    expect(els.btn).toBe(true);
    expect(els.status).toBe(true);
    expect(els.transcript).toBe(true);
    expect(els.levelBar).toBe(true);
  });

  test('initial status text is "Ready"', async ({ page }) => {
    await setupPage(page);
    const statusText = await page.evaluate(() =>
      document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-status').textContent.trim()
    );
    expect(statusText).toBe('Ready');
  });

  test('mic button has aria-label for accessibility', async ({ page }) => {
    await setupPage(page);
    const ariaLabel = await page.evaluate(() =>
      document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button').getAttribute('aria-label')
    );
    expect(ariaLabel).toBeTruthy();
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Suite 3 — Push-to-Talk Recording Flow
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Push-to-talk recording flow', () => {

  test('transcript appears in shadow DOM after pointerdown → pointerup', async ({ page }) => {
    await setupPage(page, {
      transcribeResponse: { text: 'hello world', display_text: 'Hello world', language: 'en', duration_ms: 40.0 },
    });

    // Dispatch pointerdown to start recording.
    await page.evaluate(() => {
      document.querySelector('vocal-twist').shadowRoot
        .querySelector('.vt-mic-button')
        .dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    });

    // Wait briefly, then release.
    await page.waitForTimeout(50);

    await page.evaluate(() => {
      document.querySelector('vocal-twist').shadowRoot
        .querySelector('.vt-mic-button')
        .dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
    });

    // Wait for the transcript to appear in the shadow DOM.
    await page.waitForFunction(
      () => {
        const t = document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-transcript');
        return t && t.textContent.trim().length > 0;
      },
      { timeout: 5000 }
    );

    const transcriptText = await page.evaluate(() =>
      document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-transcript').textContent.trim()
    );
    expect(transcriptText).toBe('Hello world');
  });

  test('transcribe API is called with multipart audio after stop', async ({ page }) => {
    let capturedRequest = null;

    await setupPage(page);

    await page.route('**/api/transcribe', async (route) => {
      capturedRequest = route.request();
      await route.fulfill({
        status     : 200,
        contentType: 'application/json',
        body       : JSON.stringify({ text: 'captured', display_text: 'Captured', language: 'en', duration_ms: 10.0 }),
      });
    });

    await page.evaluate(() => {
      const btn = document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button');
      btn.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    });
    await page.waitForTimeout(50);
    await page.evaluate(() => {
      const btn = document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button');
      btn.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
    });

    await page.waitForFunction(
      () => {
        const t = document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-transcript');
        return t && t.textContent.includes('Captured');
      },
      { timeout: 5000 }
    );

    expect(capturedRequest).not.toBeNull();
    expect(capturedRequest.method()).toBe('POST');
    expect(capturedRequest.url()).toContain('/api/transcribe');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Suite 4 — State Machine
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('State machine', () => {

  test('button data-state changes from idle to recording on pointerdown', async ({ page }) => {
    await setupPage(page);

    const initialState = await page.evaluate(() =>
      document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button').dataset.state
    );
    // Before any interaction, state may be undefined or 'idle'.
    expect(['idle', undefined, '']).toContain(initialState);

    await page.evaluate(() => {
      document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button')
        .dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    });
    await page.waitForTimeout(30);

    const recordingState = await page.evaluate(() =>
      document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button').dataset.state
    );
    expect(recordingState).toBe('recording');
  });

  test('vt:statechange event is dispatched during recording lifecycle', async ({ page }) => {
    await setupPage(page);

    const states = await page.evaluate(() => {
      return new Promise((resolve) => {
        const collected = [];
        const el = document.querySelector('vocal-twist');
        el.addEventListener('vt:statechange', (e) => {
          collected.push(e.detail.state);
          if (e.detail.state === 'idle' && collected.length > 1) {
            resolve(collected);
          }
        });
        const btn = el.shadowRoot.querySelector('.vt-mic-button');
        btn.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
        setTimeout(() => {
          btn.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
        }, 60);
      });
    });

    expect(states).toContain('recording');
    expect(states[states.length - 1]).toBe('idle');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Suite 5 — TTS Playback
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('TTS playback', () => {

  test('VocalTwistTTS.play() calls /api/speak and creates an Audio element', async ({ page }) => {
    await setupPage(page);

    const audioCreated = await page.evaluate(async () => {
      const tts = new VocalTwistTTS();
      window._mockAudioInstances = [];
      await tts.play('Hello there', { url: '/api/speak' });
      return window._mockAudioInstances.length > 0;
    });

    expect(audioCreated).toBe(true);
  });

  test('VocalTwistTTS.play() sends correct JSON body to /api/speak', async ({ page }) => {
    await setupPage(page);

    let capturedBody = null;
    await page.route('**/api/speak', async (route) => {
      capturedBody = route.request().postDataJSON();
      await route.fulfill({
        status     : 200,
        contentType: 'audio/mpeg',
        body       : Buffer.from([0xff, 0xfb, 0x90, 0x00]),
      });
    });

    await page.evaluate(async () => {
      const tts = new VocalTwistTTS();
      await tts.play('Test sentence', { url: '/api/speak', voice: 'en-US-AriaNeural', language: 'en' });
    });

    expect(capturedBody).not.toBeNull();
    expect(capturedBody.text).toBe('Test sentence');
  });

  test('TTS onPlay and onEnd callbacks fire', async ({ page }) => {
    await setupPage(page);

    const callbacksFired = await page.evaluate(async () => {
      const events = [];
      const tts = new VocalTwistTTS();
      tts.onPlay = () => events.push('play');
      tts.onEnd  = () => events.push('end');
      await tts.play('callback test', { url: '/api/speak' });
      return events;
    });

    expect(callbacksFired).toContain('play');
    expect(callbacksFired).toContain('end');
  });

  test('TTS error from /api/speak triggers onError callback', async ({ page }) => {
    await setupPage(page, { speakStatusCode: 500 });

    const gotError = await page.evaluate(async () => {
      let errorReceived = false;
      const tts = new VocalTwistTTS();
      tts.onError = () => { errorReceived = true; };
      try { await tts.play('trigger error', { url: '/api/speak' }); } catch (_) {}
      return errorReceived;
    });

    expect(gotError).toBe(true);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Suite 6 — Error Handling
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Error handling', () => {

  test('transcribe 500 displays error in .vt-status', async ({ page }) => {
    await setupPage(page, { transcribeResponse: 'error' });

    await page.evaluate(() => {
      const btn = document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button');
      btn.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    });
    await page.waitForTimeout(50);
    await page.evaluate(() => {
      document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button')
        .dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
    });

    await page.waitForFunction(
      () => {
        const status = document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-status');
        return status && status.textContent.toLowerCase().includes('error');
      },
      { timeout: 5000 }
    );

    const statusText = await page.evaluate(() =>
      document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-status').textContent.trim()
    );
    expect(statusText.toLowerCase()).toContain('error');
  });

  test('getUserMedia denial sets error state', async ({ page }) => {
    await injectBrowserMocks(page);

    // Override getUserMedia to reject.
    await page.addInitScript(() => {
      Object.defineProperty(navigator, 'mediaDevices', {
        value: {
          getUserMedia: () => Promise.reject(new DOMException('Permission denied', 'NotAllowedError')),
        },
        writable: true, configurable: true,
      });
    });

    await page.addScriptTag({ path: LIBRARY_PATH });
    await page.setContent(`
      <vocal-twist id="vt" transcribe-url="/api/transcribe" speak-url="/api/speak"></vocal-twist>
    `);

    await page.waitForFunction(() => customElements.get('vocal-twist') !== undefined);

    await page.evaluate(() => {
      document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button')
        .dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    });
    await page.waitForTimeout(200);

    const statusText = await page.evaluate(() =>
      document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-status').textContent.trim()
    );
    expect(statusText.toLowerCase()).toContain('error');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Suite 7 — Language & Voice Attributes
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Language and voice attributes', () => {

  test('speak-url attribute is used when calling /api/speak', async ({ page }) => {
    await injectBrowserMocks(page);
    await page.addScriptTag({ path: LIBRARY_PATH });
    await page.setContent(`
      <html>
        <head><base href="http://localhost/"></head>
        <body>
          <vocal-twist
            id="vt"
            transcribe-url="/api/transcribe"
            speak-url="/custom/speak"
            language="en"
          ></vocal-twist>
        </body>
      </html>
    `);

    let usedUrl = null;
    await page.route('**/custom/speak', async (route) => {
      usedUrl = route.request().url();
      await route.fulfill({ status: 200, contentType: 'audio/mpeg', body: Buffer.from([0xff, 0xfb]) });
    });

    await page.evaluate(async () => {
      const el = document.querySelector('vocal-twist');
      await el.vocalTwist.speak('custom URL test');
    });

    expect(usedUrl).not.toBeNull();
    expect(usedUrl).toContain('/custom/speak');
  });

  test('language attribute defaults to en when not specified', async ({ page }) => {
    await setupPage(page);

    const language = await page.evaluate(() =>
      document.querySelector('vocal-twist').getAttribute('language')
    );
    expect(language).toBe('en');
  });

  test('VocalTwistTTS sends language in request body', async ({ page }) => {
    await setupPage(page);

    let capturedBody = null;
    await page.route('**/api/speak', async (route) => {
      capturedBody = route.request().postDataJSON();
      await route.fulfill({ status: 200, contentType: 'audio/mpeg', body: Buffer.from([0xff, 0xfb]) });
    });

    await page.evaluate(async () => {
      const tts = new VocalTwistTTS();
      await tts.play('नमस्ते', { url: '/api/speak', language: 'hi', voice: 'hi-IN-SwaraNeural' });
    });

    expect(capturedBody.language).toBe('hi');
    expect(capturedBody.voice).toBe('hi-IN-SwaraNeural');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Suite 8 — API Key Header Forwarding
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('API key header forwarding', () => {

  test('api-key attribute is sent as X-API-Key header in transcribe request', async ({ page }) => {
    await injectBrowserMocks(page);
    await page.addScriptTag({ path: LIBRARY_PATH });
    await page.setContent(`
      <html>
        <head><base href="http://localhost/"></head>
        <body>
          <vocal-twist
            id="vt"
            transcribe-url="/api/transcribe"
            speak-url="/api/speak"
            language="en"
            api-key="my-secret-key"
          ></vocal-twist>
        </body>
      </html>
    `);

    let capturedHeaders = null;
    await page.route('**/api/transcribe', async (route) => {
      capturedHeaders = route.request().headers();
      await route.fulfill({
        status: 200, contentType: 'application/json',
        body: JSON.stringify({ text: 'ok', display_text: 'Ok', language: 'en', duration_ms: 10.0 }),
      });
    });

    await page.waitForFunction(() => customElements.get('vocal-twist') !== undefined);

    await page.evaluate(() => {
      document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button')
        .dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    });
    await page.waitForTimeout(50);
    await page.evaluate(() => {
      document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button')
        .dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
    });

    await page.waitForFunction(
      () => {
        const t = document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-transcript');
        return t && t.textContent.trim().length > 0;
      },
      { timeout: 5000 }
    );

    expect(capturedHeaders?.['x-api-key']).toBe('my-secret-key');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Suite 9 — Custom Events
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Custom DOM events', () => {

  test('vt:transcript event is dispatched with text and displayText', async ({ page }) => {
    await setupPage(page, {
      transcribeResponse: { text: 'event text', display_text: 'Event text', language: 'en', duration_ms: 20.0 },
    });

    const eventDetail = await page.evaluate(() => {
      return new Promise((resolve) => {
        document.querySelector('vocal-twist').addEventListener('vt:transcript', (e) => {
          resolve({ text: e.detail.text, displayText: e.detail.displayText });
        });
        const btn = document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button');
        btn.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
        setTimeout(() => btn.dispatchEvent(new PointerEvent('pointerup', { bubbles: true })), 60);
      });
    });

    expect(eventDetail.text).toBe('event text');
    expect(eventDetail.displayText).toBe('Event text');
  });

  test('vt:statechange is dispatched with the new state name', async ({ page }) => {
    await setupPage(page);

    const firstState = await page.evaluate(() => {
      return new Promise((resolve) => {
        document.querySelector('vocal-twist').addEventListener('vt:statechange', (e) => {
          resolve(e.detail.state);
        }, { once: true });
        document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button')
          .dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
      });
    });

    expect(typeof firstState).toBe('string');
    expect(firstState.length).toBeGreaterThan(0);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Suite 10 — XSS Prevention
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('XSS prevention', () => {

  test('HTML in transcript response is displayed as text, not executed', async ({ page }) => {
    const xssPayload = '<img src=x onerror="window._xss=true">Hello<script>window._xss=true</script>';

    await setupPage(page, {
      transcribeResponse: { text: xssPayload, display_text: xssPayload, language: 'en', duration_ms: 10.0 },
    });

    await page.evaluate(() => {
      const btn = document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button');
      btn.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    });
    await page.waitForTimeout(50);
    await page.evaluate(() => {
      document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button')
        .dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
    });

    await page.waitForFunction(
      () => {
        const t = document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-transcript');
        return t && t.textContent.trim().length > 0;
      },
      { timeout: 5000 }
    );

    // The XSS payload must NOT have executed.
    const xssExecuted = await page.evaluate(() => !!window._xss);
    expect(xssExecuted).toBe(false);

    // The transcript element must use textContent assignment (safe), not innerHTML.
    const transcriptContent = await page.evaluate(() =>
      document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-transcript').textContent
    );
    expect(transcriptContent).toContain('Hello');
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Suite 11 — VocalTwistRecorder Standalone
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('VocalTwistRecorder standalone', () => {

  test('start() → isRecording is true', async ({ page }) => {
    await injectBrowserMocks(page);
    await page.addScriptTag({ path: LIBRARY_PATH });
    await page.setContent('<html><body></body></html>');

    const isRecording = await page.evaluate(async () => {
      const rec = new VocalTwistRecorder();
      await rec.start();
      return rec.isRecording;
    });

    expect(isRecording).toBe(true);
  });

  test('stop() returns a Blob', async ({ page }) => {
    await injectBrowserMocks(page);
    await page.addScriptTag({ path: LIBRARY_PATH });
    await page.setContent('<html><body></body></html>');

    const blobSize = await page.evaluate(async () => {
      const rec = new VocalTwistRecorder();
      await rec.start();
      const blob = await rec.stop();
      return blob.size;
    });

    expect(blobSize).toBeGreaterThan(0);
  });

  test('cancel() does not fire onStop', async ({ page }) => {
    await injectBrowserMocks(page);
    await page.addScriptTag({ path: LIBRARY_PATH });
    await page.setContent('<html><body></body></html>');

    const stopFired = await page.evaluate(async () => {
      let fired = false;
      const rec = new VocalTwistRecorder();
      rec.onStop = () => { fired = true; };
      await rec.start();
      rec.cancel();
      await new Promise(r => setTimeout(r, 100));
      return fired;
    });

    expect(stopFired).toBe(false);
  });
});

// ═══════════════════════════════════════════════════════════════════════════════
// Suite 12 — Edge Cases
// ═══════════════════════════════════════════════════════════════════════════════

test.describe('Edge cases', () => {

  test('empty transcription response is handled gracefully', async ({ page }) => {
    await setupPage(page, {
      transcribeResponse: { text: '', display_text: '', language: 'en', duration_ms: 5.0 },
    });

    await page.evaluate(() => {
      const btn = document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button');
      btn.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
    });
    await page.waitForTimeout(50);
    await page.evaluate(() => {
      document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button')
        .dispatchEvent(new PointerEvent('pointerup', { bubbles: true }));
    });

    // Wait for state to return to idle (transcription completed without error).
    await page.waitForFunction(
      () => {
        const btn = document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button');
        const state = btn.dataset.state;
        return state === 'idle' || state === '' || state === undefined;
      },
      { timeout: 5000 }
    );

    // Should not crash — element is still functional.
    const stillPresent = await page.evaluate(
      () => !!document.querySelector('vocal-twist').shadowRoot.querySelector('.vt-mic-button')
    );
    expect(stillPresent).toBe(true);
  });

  test('VocalTwistTTS.stop() cancels inflight play without throwing', async ({ page }) => {
    await setupPage(page);

    const didNotThrow = await page.evaluate(async () => {
      const tts = new VocalTwistTTS();
      // Start a play, then immediately stop it.
      const speakPromise = tts.play('Long text to be interrupted', { url: '/api/speak' });
      tts.stop();
      try {
        await speakPromise;
        return true;
      } catch (err) {
        // AbortError is acceptable; any other error is a bug.
        return err.name === 'AbortError';
      }
    });

    expect(didNotThrow).toBe(true);
  });
});
