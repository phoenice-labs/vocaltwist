/**
 * vocal-twist.js — VocalTwist Voice Middleware
 *
 * Exports:
 *   VocalTwistRecorder  — push-to-talk microphone capture
 *   VocalTwistTTS       — text-to-speech playback
 *   VocalTwist          — main orchestrator (recorder + TTS + AmbientVAD)
 *   VocalTwistElement   — <vocal-twist> Custom Element
 *
 * Browser globals are set on window; CJS module.exports set for Node/Jest.
 * ESM export line at the bottom can be uncommented for bundler use.
 */

'use strict';

// ─── VocalTwistRecorder ───────────────────────────────────────────────────────

/**
 * Push-to-talk microphone recorder.
 *
 * Usage:
 *   const rec = new VocalTwistRecorder();
 *   rec.onLevel = (level) => updateMeter(level);  // 0–1
 *   await rec.start();
 *   const blob = await rec.stop();
 */
class VocalTwistRecorder {
  /** @type {MediaStream|null} */
  #stream = null;

  /** @type {MediaRecorder|null} */
  #recorder = null;

  /** @type {Blob[]} */
  #chunks = [];

  /** @type {AudioContext|null} */
  #audioCtx = null;

  /** @type {AnalyserNode|null} */
  #analyser = null;

  /** @type {number|null} */
  #levelTimer = null;

  /** @type {string} */
  #mimeType = 'audio/webm';

  /** Fired when recording starts. @type {(() => void)|null} */
  onStart = null;

  /** Fired with the recorded Blob when recording stops. @type {((blob: Blob) => void)|null} */
  onStop = null;

  /** Fired on error. @type {((err: Error) => void)|null} */
  onError = null;

  /** Fired continuously during recording with a normalised level 0–1. @type {((level: number) => void)|null} */
  onLevel = null;

  /** @param {{ mimeType?: string }} [options] */
  constructor(options = {}) {
    this.#mimeType = options.mimeType ?? VocalTwistRecorder.#preferredMimeType();
  }

  /** @returns {boolean} */
  get isRecording() {
    return this.#recorder?.state === 'recording';
  }

  // ── Public API ───────────────────────────────────────────────────────────────

  /**
   * Request microphone access and start recording.
   * @returns {Promise<void>}
   * @throws  {Error} if mic is denied or MediaRecorder is unavailable.
   */
  async start() {
    if (this.isRecording) return;

    try {
      this.#stream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
      this.#chunks = [];

      this.#recorder = new MediaRecorder(this.#stream, { mimeType: this.#mimeType });

      this.#recorder.ondataavailable = (e) => {
        if (e.data?.size > 0) this.#chunks.push(e.data);
      };

      this.#recorder.onerror = (e) => {
        this.onError?.(e.error ?? new Error('MediaRecorder error'));
      };

      // Level monitoring via AnalyserNode
      const ActualAudioContext = typeof AudioContext !== 'undefined'
        ? AudioContext
        : typeof webkitAudioContext !== 'undefined'
          ? webkitAudioContext // eslint-disable-line no-undef
          : null;

      if (ActualAudioContext) {
        this.#audioCtx = new ActualAudioContext();
        const source    = this.#audioCtx.createMediaStreamSource(this.#stream);
        this.#analyser  = this.#audioCtx.createAnalyser();
        this.#analyser.fftSize = 256;
        source.connect(this.#analyser);
        this.#startLevelMonitor();
      }

      this.#recorder.start(100); // collect in 100 ms chunks
      this.onStart?.();
    } catch (err) {
      this.#cleanupResources();
      this.onError?.(err);
      throw err;
    }
  }

  /**
   * Stop recording and resolve with the audio Blob.
   * @returns {Promise<Blob>}
   */
  stop() {
    return new Promise((resolve, reject) => {
      if (!this.#recorder || this.#recorder.state === 'inactive') {
        reject(new Error('VocalTwistRecorder: not currently recording'));
        return;
      }

      this.#stopLevelMonitor();

      this.#recorder.onstop = () => {
        const blob = new Blob(this.#chunks, { type: this.#mimeType });
        this.#cleanupResources();
        this.onStop?.(blob);
        resolve(blob);
      };

      this.#recorder.stop();
    });
  }

  /**
   * Abort recording without returning audio. Safe to call at any time.
   */
  cancel() {
    this.#stopLevelMonitor();
    if (this.#recorder && this.#recorder.state !== 'inactive') {
      this.#recorder.onstop = null;
      this.#recorder.stop();
    }
    this.#cleanupResources();
  }

  // ── Internals ────────────────────────────────────────────────────────────────

  #cleanupResources() {
    this.#stopLevelMonitor();
    if (this.#stream) {
      this.#stream.getTracks().forEach((t) => t.stop());
      this.#stream = null;
    }
    if (this.#audioCtx) {
      try { this.#audioCtx.close(); } catch (_) { /* best-effort */ }
      this.#audioCtx = null;
    }
    this.#analyser = null;
    this.#chunks   = [];
    this.#recorder = null;
  }

  #startLevelMonitor() {
    if (!this.#analyser) return;
    const data = new Uint8Array(this.#analyser.frequencyBinCount);
    const raf  = typeof requestAnimationFrame !== 'undefined'
      ? (cb) => requestAnimationFrame(cb)
      : (cb) => setTimeout(cb, 16);

    const tick = () => {
      if (!this.#analyser) return;
      this.#analyser.getByteFrequencyData(data);
      const sum = data.reduce((s, v) => s + v * v, 0);
      const rms = Math.sqrt(sum / data.length) / 255;
      this.onLevel?.(Math.min(1, rms));
      this.#levelTimer = raf(tick);
    };

    this.#levelTimer = raf(tick);
  }

  #stopLevelMonitor() {
    if (this.#levelTimer !== null) {
      if (typeof cancelAnimationFrame !== 'undefined') {
        cancelAnimationFrame(this.#levelTimer);
      } else {
        clearTimeout(this.#levelTimer);
      }
      this.#levelTimer = null;
    }
  }

  static #preferredMimeType() {
    if (typeof MediaRecorder === 'undefined') return 'audio/webm';
    const candidates = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/ogg;codecs=opus',
      'audio/mp4',
    ];
    return candidates.find((t) => MediaRecorder.isTypeSupported(t)) ?? 'audio/webm';
  }
}

// ─── VocalTwistTTS ────────────────────────────────────────────────────────────

/**
 * Text-to-speech playback via the /api/speak endpoint.
 *
 * Usage:
 *   const tts = new VocalTwistTTS();
 *   tts.onEnd = () => console.log('done');
 *   await tts.play('Hello world', { url: '/api/speak', voice: 'alloy' });
 *   tts.stop();
 */
class VocalTwistTTS {
  /** @type {HTMLAudioElement|null} */
  #audio = null;

  /** @type {string|null} Blob URL to revoke after playback. */
  #blobUrl = null;

  /** @type {boolean} */
  #playing = false;

  /** @type {AbortController|null} */
  #fetchAbort = null;

  /** Resolves the awaited play() promise from stop(). @type {(() => void)|null} */
  #playResolve = null;

  /** Fired when playback starts.  @type {(() => void)|null} */
  onPlay = null;

  /** Fired when playback ends naturally or via stop(). @type {(() => void)|null} */
  onEnd = null;

  /** Fired on error. @type {((err: Error) => void)|null} */
  onError = null;

  /** @returns {boolean} */
  get isPlaying() {
    return this.#playing;
  }

  // ── Public API ───────────────────────────────────────────────────────────────

  /**
   * Fetch TTS audio and play it.
   * If already playing, the current audio is stopped first.
   *
   * @param {string} text
   * @param {{ url?: string, language?: string, voice?: string, apiKey?: string }} [options]
   * @returns {Promise<void>} Resolves when playback finishes.
   */
  async play(text, options = {}) {
    if (this.#playing) this.stop();

    const { url = '/api/speak', language, voice, apiKey } = options;

    this.#fetchAbort = new AbortController();

    try {
      const headers = { 'Content-Type': 'application/json' };
      if (apiKey) headers['X-API-Key'] = apiKey;

      const response = await fetch(url, {
        method  : 'POST',
        headers,
        body    : JSON.stringify({ text, language, voice }),
        signal  : this.#fetchAbort.signal,
      });

      if (!response.ok) {
        throw new Error(`VocalTwistTTS: HTTP ${response.status} ${response.statusText}`);
      }

      const blob      = await response.blob();
      this.#blobUrl   = URL.createObjectURL(blob);
      this.#audio     = new Audio(this.#blobUrl);
      this.#playing   = true;

      await new Promise((resolve, reject) => {
        this.#playResolve = resolve;

        this.#audio.onended = () => {
          this.#playing     = false;
          this.#playResolve = null;
          this.#revokeBlobUrl();
          this.onEnd?.();
          resolve();
        };

        this.#audio.onerror = () => {
          this.#playing     = false;
          this.#playResolve = null;
          this.#revokeBlobUrl();
          const err = new Error('VocalTwistTTS: audio playback error');
          this.onError?.(err);
          reject(err);
        };

        this.onPlay?.();
        this.#audio.play().catch(reject);
      });
    } catch (err) {
      this.#playing   = false;
      this.#fetchAbort = null;
      if (err.name !== 'AbortError') {
        this.onError?.(err);
        throw err;
      }
    } finally {
      this.#fetchAbort = null;
    }
  }

  /**
   * Immediately stop any active TTS playback and abort any pending fetch.
   */
  stop() {
    if (this.#fetchAbort) {
      this.#fetchAbort.abort();
      this.#fetchAbort = null;
    }

    // Capture and clear resolver before mutating state so the awaited play()
    // promise settles without triggering a second onEnd via onended.
    const resolvePlay    = this.#playResolve;
    this.#playResolve    = null;

    if (this.#audio) {
      this.#audio.pause();
      this.#audio.onended = null;
      this.#audio.onerror = null;
      this.#audio = null;
    }

    this.#revokeBlobUrl();

    if (this.#playing) {
      this.#playing = false;
      this.onEnd?.();
    }

    resolvePlay?.(); // unblock the awaited play() promise
  }

  // ── Internal ─────────────────────────────────────────────────────────────────

  #revokeBlobUrl() {
    if (this.#blobUrl) {
      URL.revokeObjectURL(this.#blobUrl);
      this.#blobUrl = null;
    }
  }
}

// ─── VocalTwist (Main Orchestrator) ──────────────────────────────────────────

/**
 * Main VocalTwist orchestrator — combines push-to-talk recording, transcription,
 * TTS playback, and ambient (always-on) listening into a single API.
 *
 * Usage:
 *   const vt = new VocalTwist({
 *     onTranscript: (text) => console.log(text),
 *     onStateChange: (state) => updateUI(state),
 *   });
 *   await vt.startRecording();   // user speaks
 *   await vt.stopRecording();    // auto-transcribes
 *   await vt.speak('Response');  // TTS
 *   vt.destroy();
 */
class VocalTwist {
  /** @type {object} Resolved config. */
  #config;

  /** @type {VocalTwistRecorder} */
  #recorder;

  /** @type {VocalTwistTTS} */
  #tts;

  /** @type {object|null} AmbientVAD instance. */
  #ambientVAD = null;

  /** @type {'idle'|'recording'|'transcribing'|'speaking'|'ambient-listening'|'ambient-buffering'|'ambient-transcribing'} */
  #state = 'idle';

  /**
   * @param {object}   [config]
   * @param {string}   [config.transcribeUrl='/api/transcribe']
   * @param {string}   [config.speakUrl='/api/speak']
   * @param {string}   [config.ambientUrl='/api/transcribe-ambient']
   * @param {string}   [config.language='en']
   * @param {string}   [config.voice]            TTS voice name.
   * @param {string}   [config.apiKey]           Sent as X-API-Key header.
   * @param {boolean}  [config.ambientMode=false] Start in ambient mode.
   * @param {number}   [config.silenceMs=15000]  Silence window for ambient mode.
   * @param {number}   [config.maxBufferMs=30000] Max buffer for ambient mode.
   * @param {Function} [config.onTranscript]     (text, displayText?) => void
   * @param {Function} [config.onStateChange]    (state) => void
   * @param {Function} [config.onSpeechStart]    () => void — fired in ambient mode
   * @param {Function} [config.onError]          (err) => void
   * @param {Function} [config.onTTSStart]       () => void
   * @param {Function} [config.onTTSEnd]         () => void
   */
  constructor(config = {}) {
    this.#config = {
      transcribeUrl : config.transcribeUrl ?? '/api/transcribe',
      speakUrl      : config.speakUrl      ?? '/api/speak',
      ambientUrl    : config.ambientUrl    ?? '/api/transcribe-ambient',
      language      : config.language      ?? 'en',
      voice         : config.voice         ?? null,
      apiKey        : config.apiKey        ?? null,
      ambientMode   : config.ambientMode   ?? false,
      silenceMs     : config.silenceMs     ?? 15_000,
      maxBufferMs   : config.maxBufferMs   ?? 30_000,
      onTranscript  : config.onTranscript  ?? null,
      onStateChange : config.onStateChange ?? null,
      onSpeechStart : config.onSpeechStart ?? null,
      onError       : config.onError       ?? null,
      onTTSStart    : config.onTTSStart    ?? null,
      onTTSEnd      : config.onTTSEnd      ?? null,
    };

    this.#recorder = new VocalTwistRecorder();
    this.#tts      = new VocalTwistTTS();

    this.#recorder.onError = (err) => this.#handleError(err);

    this.#tts.onPlay = () => {
      this.#config.onTTSStart?.();
    };

    this.#tts.onEnd = () => {
      if (this.#state === 'speaking') this.#setState('idle');
      this.#config.onTTSEnd?.();
    };

    this.#tts.onError = (err) => this.#handleError(err);
  }

  // ── Public API ───────────────────────────────────────────────────────────────

  /**
   * Current orchestrator state.
   * @returns {'idle'|'recording'|'transcribing'|'speaking'|'ambient-listening'|'ambient-buffering'|'ambient-transcribing'}
   */
  get state() {
    return this.#state;
  }

  /**
   * Audio level callback during recording (0–1). Useful for level meters.
   * @type {((level: number) => void)|null}
   */
  set onLevel(fn) {
    this.#recorder.onLevel = fn;
  }

  /**
   * Start push-to-talk recording. No-op if not in idle state.
   * @returns {Promise<void>}
   */
  async startRecording() {
    if (this.#state !== 'idle') return;
    this.#setState('recording');
    try {
      await this.#recorder.start();
    } catch (err) {
      this.#setState('idle');
      this.#config.onError?.(err);
      throw err;
    }
  }

  /**
   * Stop push-to-talk recording and automatically transcribe the result.
   * @returns {Promise<void>}
   */
  async stopRecording() {
    if (this.#state !== 'recording') return;
    this.#setState('transcribing');
    try {
      const blob = await this.#recorder.stop();
      await this.transcribe(blob);
    } catch (err) {
      this.#handleError(err);
    }
  }

  /**
   * POST an audio Blob to the transcription endpoint and call onTranscript.
   * @param {Blob} audioBlob
   * @returns {Promise<string>} Transcribed text.
   */
  async transcribe(audioBlob) {
    if (this.#state !== 'transcribing') this.#setState('transcribing');

    try {
      const headers = {};
      if (this.#config.apiKey) headers['X-API-Key'] = this.#config.apiKey;

      const formData = new FormData();
      formData.append('audio', audioBlob, 'recording.webm');
      formData.append('language', this.#config.language);

      const response = await fetch(this.#config.transcribeUrl, {
        method : 'POST',
        headers,
        body   : formData,
      });

      if (!response.ok) {
        throw new Error(`VocalTwist: transcription HTTP ${response.status}`);
      }

      const data        = await response.json();
      const text        = data.text       ?? data.transcript  ?? '';
      const displayText = data.displayText ?? data.display_text ?? text;

      this.#setState('idle');
      if (text) this.#config.onTranscript?.(text, displayText);
      return text;
    } catch (err) {
      this.#handleError(err);
      throw err;
    }
  }

  /**
   * Fetch TTS audio for `text` and play it.
   * If the user is speaking (ambient onSpeechStart), call stop() on the TTS
   * instance before calling speak() again.
   * @param {string} text
   * @returns {Promise<void>}
   */
  async speak(text) {
    if (this.#tts.isPlaying) this.#tts.stop();
    this.#setState('speaking');

    try {
      await this.#tts.play(text, {
        url      : this.#config.speakUrl,
        language : this.#config.language,
        voice    : this.#config.voice,
        apiKey   : this.#config.apiKey,
      });
    } catch (err) {
      if (this.#state === 'speaking') this.#setState('idle');
      this.#config.onError?.(err);
      throw err;
    }
  }

  /**
   * Start always-on ambient listening mode using AmbientVAD.
   * Requires window.AmbientVAD to be loaded (ambient-vad.js).
   * @returns {Promise<void>}
   */
  async startAmbient() {
    const AmbientVADClass = typeof AmbientVAD !== 'undefined'
      ? AmbientVAD // eslint-disable-line no-undef
      : typeof globalThis.AmbientVAD !== 'undefined'
        ? globalThis.AmbientVAD
        : null;

    if (!AmbientVADClass) {
      throw new Error('VocalTwist: AmbientVAD not loaded — include ambient-vad.js first.');
    }

    if (this.#ambientVAD) {
      this.#ambientVAD.stop();
      this.#ambientVAD = null;
    }

    this.#ambientVAD = new AmbientVADClass({
      transcribeUrl : this.#config.ambientUrl,
      language      : this.#config.language,
      silenceMs     : this.#config.silenceMs,
      maxBufferMs   : this.#config.maxBufferMs,

      onTranscript: (text, displayText) => {
        this.#config.onTranscript?.(text, displayText);
      },

      onStateChange: (vadState) => {
        const stateMap = {
          listening    : 'ambient-listening',
          buffering    : 'ambient-buffering',
          transcribing : 'ambient-transcribing',
          idle         : 'idle',
        };
        const mapped = stateMap[vadState] ?? vadState;
        this.#setState(mapped);
      },

      onSpeechStart: () => {
        // Full-duplex: interrupt TTS when user starts speaking
        if (this.#tts.isPlaying) this.#tts.stop();
        this.#config.onSpeechStart?.();
      },

      onError: (err) => this.#config.onError?.(err),
    });

    await this.#ambientVAD.start();
  }

  /**
   * Stop ambient listening mode.
   */
  stopAmbient() {
    if (this.#ambientVAD) {
      this.#ambientVAD.stop();
      this.#ambientVAD = null;
    }
    if (this.#state.startsWith('ambient-')) {
      this.#setState('idle');
    }
  }

  /**
   * Update the active language for transcription and TTS.
   * @param {string} lang BCP-47 language code, e.g. 'en', 'fr', 'es'.
   */
  setLanguage(lang) {
    this.#config.language = lang;
  }

  /**
   * Update the TTS voice.
   * @param {string} voice
   */
  setVoice(voice) {
    this.#config.voice = voice;
  }

  /**
   * Release all resources: stop recording, TTS, and ambient listening.
   */
  destroy() {
    this.stopAmbient();
    this.#recorder.cancel();
    this.#tts.stop();
    this.#setState('idle');
  }

  // ── Internal ─────────────────────────────────────────────────────────────────

  #setState(state) {
    this.#state = state;
    this.#config.onStateChange?.(state);
  }

  #handleError(err) {
    this.#setState('idle');
    this.#config.onError?.(err);
  }
}

// ─── VocalTwistElement (<vocal-twist>) ───────────────────────────────────────

/**
 * <vocal-twist> Custom Element.
 *
 * Attributes:
 *   transcribe-url  — STT endpoint (default: /api/transcribe)
 *   speak-url       — TTS endpoint (default: /api/speak)
 *   ambient-url     — ambient STT endpoint (default: /api/transcribe-ambient)
 *   language        — BCP-47 code (default: en)
 *   voice           — TTS voice name
 *   ambient         — boolean; enables ambient mode when present
 *   api-key         — X-API-Key header value
 *
 * Custom Events (bubbles: true):
 *   vt:transcript   — detail: { text, displayText }
 *   vt:speaking     — fired when TTS begins
 *   vt:error        — detail: { error }
 *   vt:statechange  — detail: { state }
 *
 * Programmatic access:
 *   document.querySelector('vocal-twist').vocalTwist.speak('Hello');
 */
class VocalTwistElement extends HTMLElement {
  static get observedAttributes() {
    return ['transcribe-url', 'speak-url', 'ambient-url', 'language', 'voice', 'ambient', 'api-key'];
  }

  /** @type {VocalTwist|null} */
  #vt = null;

  /** @type {ShadowRoot} */
  #shadow;

  /** @type {HTMLButtonElement|null} */
  #micBtn = null;

  /** @type {HTMLElement|null} */
  #status = null;

  /** @type {HTMLElement|null} */
  #transcript = null;

  /** @type {HTMLElement|null} */
  #levelFill = null;

  /** @type {boolean} Tracks pointer-hold recording. */
  #isRecording = false;

  constructor() {
    super();
    this.#shadow = this.attachShadow({ mode: 'open' });
  }

  connectedCallback() {
    this.#render();
    this.#initVocalTwist();
  }

  disconnectedCallback() {
    this.#vt?.destroy();
    this.#vt = null;
  }

  attributeChangedCallback(name, oldVal, newVal) {
    if (oldVal === newVal || !this.#vt) return;
    if (name === 'language') this.#vt.setLanguage(newVal);
    if (name === 'voice')    this.#vt.setVoice(newVal);
  }

  /** Direct access to the underlying VocalTwist instance. */
  get vocalTwist() {
    return this.#vt;
  }

  // ── Shadow DOM ───────────────────────────────────────────────────────────────

  #render() {
    this.#shadow.innerHTML = `
      <style>
        :host {
          --vt-primary:        #4f46e5;
          --vt-primary-hover:  #4338ca;
          --vt-primary-active: #3730a3;
          --vt-danger:         #ef4444;
          --vt-success:        #22c55e;
          --vt-warn:           #f59e0b;
          --vt-bg:             #ffffff;
          --vt-surface:        #f8fafc;
          --vt-border:         #e2e8f0;
          --vt-text:           #1e293b;
          --vt-text-muted:     #64748b;
          --vt-btn-size:       3.5rem;
          --vt-radius:         9999px;
          --vt-radius-card:    0.75rem;
          --vt-gap:            0.75rem;
          --vt-font:           system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
          --vt-font-size:      0.875rem;
          --vt-font-size-sm:   0.75rem;
          --vt-pulse-color:    rgba(79, 70, 229, 0.35);
          display: inline-block;
          font-family: var(--vt-font);
          font-size: var(--vt-font-size);
          color: var(--vt-text);
        }
        @media (prefers-color-scheme: dark) {
          :host {
            --vt-bg:         #0f172a;
            --vt-surface:    #1e293b;
            --vt-border:     #334155;
            --vt-text:       #f1f5f9;
            --vt-text-muted: #94a3b8;
          }
        }
        .vt-container {
          display: flex; flex-direction: column; align-items: center;
          gap: var(--vt-gap); padding: 1rem;
          background: var(--vt-bg); border: 1px solid var(--vt-border);
          border-radius: var(--vt-radius-card); min-width: 180px; max-width: 420px;
        }
        .vt-mic-button {
          position: relative; display: flex; align-items: center;
          justify-content: center; width: var(--vt-btn-size);
          height: var(--vt-btn-size); border: none;
          border-radius: var(--vt-radius); background: var(--vt-primary);
          color: #fff; cursor: pointer; outline: none;
          transition: background 200ms ease, transform 200ms ease;
          user-select: none; touch-action: none;
        }
        .vt-mic-button svg { width: 1.5rem; height: 1.5rem; fill: currentColor; pointer-events: none; }
        .vt-mic-button:hover  { background: var(--vt-primary-hover); transform: scale(1.05); }
        .vt-mic-button:active { background: var(--vt-primary-active); transform: scale(0.97); }
        .vt-mic-button:focus-visible {
          box-shadow: 0 0 0 3px var(--vt-bg), 0 0 0 5px var(--vt-primary);
        }
        .vt-mic-button[data-state="recording"] {
          background: var(--vt-danger);
          animation: vt-pulse-record 1s ease-in-out infinite;
        }
        .vt-mic-button[data-state="ambient-listening"],
        .vt-mic-button[data-state="ambient-buffering"] { background: var(--vt-success); }
        .vt-mic-button[data-state="transcribing"],
        .vt-mic-button[data-state="ambient-transcribing"],
        .vt-mic-button[data-state="speaking"] {
          background: var(--vt-warn);
          animation: vt-throb 1.2s ease-in-out infinite;
          pointer-events: none;
        }
        .vt-status {
          font-size: var(--vt-font-size-sm); color: var(--vt-text-muted);
          text-align: center; min-height: 1.2em;
        }
        .vt-transcript {
          width: 100%; min-height: 2.4em; padding: 0.5rem 0.625rem;
          background: var(--vt-surface); border: 1px solid var(--vt-border);
          border-radius: 0.375rem; font-size: var(--vt-font-size);
          color: var(--vt-text); line-height: 1.5; word-break: break-word;
          white-space: pre-wrap; box-sizing: border-box;
        }
        .vt-transcript:empty::before {
          content: 'Transcript will appear here\u2026';
          color: var(--vt-text-muted); font-style: italic;
        }
        .vt-level-bar {
          width: 100%; height: 3px; background: var(--vt-border);
          border-radius: var(--vt-radius); overflow: hidden;
        }
        .vt-level-fill {
          height: 100%; width: 0%; background: var(--vt-primary);
          border-radius: var(--vt-radius); transition: width 80ms linear;
        }
        @keyframes vt-pulse-record {
          0%, 100% { box-shadow: 0 0 0 0   rgba(239,68,68,.4); }
          50%       { box-shadow: 0 0 0 12px rgba(239,68,68,0); }
        }
        @keyframes vt-throb {
          0%, 100% { opacity: 1;   transform: scale(1); }
          50%       { opacity: 0.8; transform: scale(0.97); }
        }
      </style>
      <div class="vt-container">
        <button class="vt-mic-button" type="button" aria-label="Hold to record">
          <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
            <path d="M12 14c1.66 0 3-1.34 3-3V5c0-1.66-1.34-3-3-3S9 3.34 9 5v6c0 1.66 1.34 3 3 3zm5.91-3c-.49 0-.9.36-.98.85C16.52 14.2 14.47 16 12 16s-4.52-1.8-4.93-4.15c-.08-.49-.49-.85-.98-.85-.61 0-1.09.54-1 1.14.49 3 2.89 5.35 5.91 5.78V20c0 .55.45 1 1 1s1-.45 1-1v-2.08c3.02-.43 5.42-2.78 5.91-5.78.1-.6-.39-1.14-1-1.14z"/>
          </svg>
        </button>
        <div class="vt-status">Ready</div>
        <div class="vt-level-bar"><div class="vt-level-fill"></div></div>
        <div class="vt-transcript"></div>
      </div>
    `;

    this.#micBtn    = this.#shadow.querySelector('.vt-mic-button');
    this.#status    = this.#shadow.querySelector('.vt-status');
    this.#transcript = this.#shadow.querySelector('.vt-transcript');
    this.#levelFill = this.#shadow.querySelector('.vt-level-fill');

    // Hold-to-record gesture
    this.#micBtn.addEventListener('pointerdown', () => this.#handlePtrDown());
    this.#micBtn.addEventListener('pointerup',   () => this.#handlePtrUp());
    this.#micBtn.addEventListener('pointerleave', () => {
      if (this.#isRecording) this.#handlePtrUp();
    });
  }

  #initVocalTwist() {
    const ambient = this.hasAttribute('ambient');

    this.#vt = new VocalTwist({
      transcribeUrl : this.getAttribute('transcribe-url') ?? undefined,
      speakUrl      : this.getAttribute('speak-url')      ?? undefined,
      ambientUrl    : this.getAttribute('ambient-url')    ?? undefined,
      language      : this.getAttribute('language')       ?? 'en',
      voice         : this.getAttribute('voice')          ?? undefined,
      apiKey        : this.getAttribute('api-key')        ?? undefined,
      ambientMode   : ambient,

      onTranscript: (text, displayText) => {
        if (this.#transcript) this.#transcript.textContent = displayText ?? text;
        this.#fireEvent('vt:transcript', { text, displayText: displayText ?? text });
      },

      onStateChange: (state) => {
        this.#updateUI(state);
        this.#fireEvent('vt:statechange', { state });
      },

      onError: (err) => {
        if (this.#status) this.#status.textContent = `Error: ${err.message}`;
        this.#fireEvent('vt:error', { error: err });
      },

      onTTSStart: () => {
        this.#fireEvent('vt:speaking', {});
      },
    });

    // Expose level meter on the custom element
    this.#vt.onLevel = (level) => {
      if (this.#levelFill) this.#levelFill.style.width = `${(level * 100).toFixed(1)}%`;
    };

    if (ambient) {
      this.#vt.startAmbient().catch((err) => {
        if (this.#status) this.#status.textContent = `Ambient error: ${err.message}`;
      });
    }
  }

  // ── UI helpers ───────────────────────────────────────────────────────────────

  #handlePtrDown() {
    if (this.#vt?.state !== 'idle') return;
    this.#isRecording = true;
    this.#vt.startRecording().catch(() => { this.#isRecording = false; });
  }

  #handlePtrUp() {
    if (!this.#isRecording) return;
    this.#isRecording = false;
    if (this.#vt?.state === 'recording') {
      this.#vt.stopRecording();
    }
  }

  #updateUI(state) {
    const labels = {
      'idle'                  : 'Ready',
      'recording'             : 'Recording…',
      'transcribing'          : 'Transcribing…',
      'speaking'              : 'Speaking…',
      'ambient-listening'     : 'Listening…',
      'ambient-buffering'     : 'Processing…',
      'ambient-transcribing'  : 'Transcribing…',
    };

    if (this.#micBtn) this.#micBtn.dataset.state = state;
    if (this.#status) {
      this.#status.textContent = labels[state] ?? state;
      this.#status.dataset.state = state;
    }

    if (state !== 'recording' && this.#levelFill) {
      this.#levelFill.style.width = '0%';
    }
  }

  #fireEvent(type, detail) {
    this.dispatchEvent(new CustomEvent(type, { bubbles: true, composed: true, detail }));
  }
}

// ─── Registration & Exports ───────────────────────────────────────────────────

if (typeof customElements !== 'undefined' && !customElements.get('vocal-twist')) {
  customElements.define('vocal-twist', VocalTwistElement);
}

if (typeof globalThis !== 'undefined') {
  globalThis.VocalTwist         = VocalTwist;
  globalThis.VocalTwistRecorder = VocalTwistRecorder;
  globalThis.VocalTwistTTS      = VocalTwistTTS;
}

if (typeof module !== 'undefined' && typeof module.exports !== 'undefined') {
  module.exports = { VocalTwist, VocalTwistRecorder, VocalTwistTTS, VocalTwistElement };
}

// ESM — uncomment for bundler / native ESM use:
// export { VocalTwist, VocalTwistRecorder, VocalTwistTTS, VocalTwistElement };
