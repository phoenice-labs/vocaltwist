/**
 * AmbientVAD — Portable Ambient Listening Module
 * VocalTwist edition: zero project-specific dependencies.
 *
 * Requires @ricky0123/vad-web + onnxruntime-web loaded via CDN or bundler.
 *
 * Quick start:
 *   const av = new AmbientVAD({
 *     transcribeUrl : '/api/transcribe-ambient',
 *     language      : 'en',
 *     silenceMs     : 15000,
 *     maxBufferMs   : 30000,
 *     onTranscript  : (text, displayText) => {},
 *     onStateChange : (state) => {},  // idle|listening|buffering|transcribing
 *     onSpeechStart : () => {},
 *     onError       : (err) => {},
 *   });
 *   await av.start();
 *   av.stop();
 */

'use strict';

class AmbientVAD {
  /** @type {'idle'|'listening'|'buffering'|'transcribing'} */
  #state = 'idle';

  /** @type {boolean} Set to true on stop() to halt all async paths. */
  #stopped = false;

  /** @type {any} vad-web MicVAD instance */
  #vad = null;

  /** @type {Float32Array[]} Accumulated speech segments awaiting transcription. */
  #audioChunks = [];

  /** @type {ReturnType<typeof setTimeout>|null} */
  #silenceTimer = null;

  /** @type {ReturnType<typeof setTimeout>|null} */
  #maxBufferTimer = null;

  /** @type {AbortController|null} In-flight transcription request controller. */
  #abortController = null;

  /** @type {boolean} Guards against overlapping transcription requests. */
  #transcribing = false;

  /** @type {number} VAD-web always outputs 16 kHz mono. */
  #sampleRate = 16000;

  /**
   * @param {object}   options
   * @param {string}   [options.transcribeUrl='/api/transcribe-ambient']
   * @param {string}   [options.language='en']
   * @param {number}   [options.silenceMs=15000]  Post-speech silence before flush.
   * @param {number}   [options.maxBufferMs=30000] Hard max buffer before forced flush.
   * @param {Function} [options.onTranscript]   (text: string, displayText: string) => void
   * @param {Function} [options.onStateChange]  (state: string) => void
   * @param {Function} [options.onSpeechStart]  () => void
   * @param {Function} [options.onError]        (err: Error) => void
   */
  constructor(options = {}) {
    this._opts = {
      transcribeUrl : options.transcribeUrl  ?? '/api/transcribe-ambient',
      language      : options.language       ?? 'en',
      silenceMs     : options.silenceMs      ?? 15_000,
      maxBufferMs   : options.maxBufferMs    ?? 30_000,
      onTranscript  : options.onTranscript   ?? (() => {}),
      onStateChange : options.onStateChange  ?? (() => {}),
      onSpeechStart : options.onSpeechStart  ?? (() => {}),
      onError       : options.onError        ?? (() => {}),
    };
  }

  // ─── Public API ─────────────────────────────────────────────────────────────

  /**
   * Returns true when the browser has the necessary APIs to run AmbientVAD.
   * Both @ricky0123/vad-web (window.vad) and onnxruntime-web (window.ort) must
   * be loaded before this returns true.
   * @returns {boolean}
   */
  static isSupported() {
    return (
      typeof globalThis !== 'undefined' &&
      typeof globalThis.vad?.MicVAD?.new === 'function' &&
      typeof globalThis.ort !== 'undefined' &&
      typeof navigator?.mediaDevices?.getUserMedia === 'function'
    );
  }

  /** @returns {'idle'|'listening'|'buffering'|'transcribing'} */
  get state() {
    return this.#state;
  }

  /**
   * Encode an array of Float32Array speech segments as a mono 16-bit PCM WAV Blob.
   * Exposed as a static method for testability.
   * @param {Float32Array[]} chunks     Array of audio segments from vad-web.
   * @param {number}         sampleRate Samples per second (default: 16000).
   * @returns {Blob}
   */
  static encodeWav(chunks, sampleRate = 16000) {
    const totalLen = chunks.reduce((n, c) => n + c.length, 0);
    const samples  = new Float32Array(totalLen);
    let pos = 0;
    for (const c of chunks) {
      samples.set(c, pos);
      pos += c.length;
    }

    const numChannels   = 1;
    const bitsPerSample = 16;
    const byteRate      = sampleRate * numChannels * (bitsPerSample / 8);
    const blockAlign    = numChannels * (bitsPerSample / 8);
    const dataSize      = samples.length * 2; // 2 bytes per int16 sample

    const buf  = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buf);

    // RIFF header
    AmbientVAD.#writeStr(view, 0,  'RIFF');
    view.setUint32(4,  36 + dataSize, true);
    AmbientVAD.#writeStr(view, 8,  'WAVE');

    // fmt  chunk
    AmbientVAD.#writeStr(view, 12, 'fmt ');
    view.setUint32(16, 16,            true); // chunk size
    view.setUint16(20, 1,             true); // PCM = 1
    view.setUint16(22, numChannels,   true);
    view.setUint32(24, sampleRate,    true);
    view.setUint32(28, byteRate,      true);
    view.setUint16(32, blockAlign,    true);
    view.setUint16(34, bitsPerSample, true);

    // data chunk
    AmbientVAD.#writeStr(view, 36, 'data');
    view.setUint32(40, dataSize, true);

    // Float32 → Int16 PCM conversion
    let offset = 44;
    for (let i = 0; i < samples.length; i++) {
      const s = Math.max(-1, Math.min(1, samples[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
      offset += 2;
    }

    return new Blob([buf], { type: 'audio/wav' });
  }

  /** @param {DataView} view  @param {number} offset  @param {string} str */
  static #writeStr(view, offset, str) {
    for (let i = 0; i < str.length; i++) {
      view.setUint8(offset + i, str.charCodeAt(i));
    }
  }

  /**
   * Start ambient voice detection and microphone capture.
   * Transitions: idle → listening
   * @returns {Promise<void>}
   * @throws {Error} if the browser is unsupported or mic is denied.
   */
  async start() {
    if (this.#state !== 'idle') return;
    if (!AmbientVAD.isSupported()) {
      throw new Error(
        'AmbientVAD: browser not supported — ensure vad-web and onnxruntime-web are loaded.'
      );
    }

    this.#stopped    = false;
    this.#audioChunks = [];
    this.#setState('listening');

    try {
      // Suppress benign graph-optimisation warnings from onnxruntime-web
      if (typeof globalThis.ort !== 'undefined') {
        globalThis.ort.env.logLevel = 'error';
      }

      this.#vad = await globalThis.vad.MicVAD.new({
        onSpeechStart: () => {
          if (this.#stopped) return;
          this._opts.onSpeechStart();
          // New utterance begins — cancel pending silence flush
          this.#clearSilenceTimer();
          if (this.#state === 'listening') {
            this.#setState('buffering');
          }
        },

        onSpeechEnd: (audio) => {
          if (this.#stopped) return;
          // audio is a Float32Array at 16 kHz from vad-web
          this.#audioChunks.push(new Float32Array(audio));
          if (this.#state !== 'transcribing') {
            this.#setState('buffering');
          }
          // Restart silence window after each utterance segment
          this.#startSilenceTimer();
        },

        onVADMisfire: () => { /* intentionally ignored */ },
      });

      this.#vad.start();
      this.#startMaxBufferTimer();
    } catch (err) {
      this.#stopped = true;
      this.#setState('idle');
      this._opts.onError(err);
      throw err;
    }
  }

  /**
   * Stop ambient detection and release all resources immediately.
   * Any in-flight transcription request is aborted.
   */
  stop() {
    if (this.#stopped) return;
    this.#stopped = true;

    this.#clearTimers();

    if (this.#abortController) {
      this.#abortController.abort();
      this.#abortController = null;
    }

    if (this.#vad) {
      try { this.#vad.destroy(); } catch (_) { /* best-effort */ }
      this.#vad = null;
    }

    this.#audioChunks = [];
    this.#transcribing = false;
    this.#setState('idle');
  }

  // ─── Internal state machine ──────────────────────────────────────────────────

  /** @param {'idle'|'listening'|'buffering'|'transcribing'} state */
  #setState(state) {
    this.#state = state;
    this._opts.onStateChange(state);
  }

  #startSilenceTimer() {
    this.#clearSilenceTimer();
    this.#silenceTimer = setTimeout(() => {
      this.#silenceTimer = null;
      if (!this.#stopped && this.#audioChunks.length > 0) {
        this.#transcribeBuffer();
      }
    }, this._opts.silenceMs);
  }

  #startMaxBufferTimer() {
    this.#clearMaxBufferTimer();
    this.#maxBufferTimer = setTimeout(() => {
      this.#maxBufferTimer = null;
      if (!this.#stopped && this.#audioChunks.length > 0) {
        this.#transcribeBuffer();
      }
    }, this._opts.maxBufferMs);
  }

  #clearSilenceTimer() {
    if (this.#silenceTimer !== null) {
      clearTimeout(this.#silenceTimer);
      this.#silenceTimer = null;
    }
  }

  #clearMaxBufferTimer() {
    if (this.#maxBufferTimer !== null) {
      clearTimeout(this.#maxBufferTimer);
      this.#maxBufferTimer = null;
    }
  }

  #clearTimers() {
    this.#clearSilenceTimer();
    this.#clearMaxBufferTimer();
  }

  // ─── Transcription ───────────────────────────────────────────────────────────

  /**
   * Drain the audio buffer and POST it to the transcription endpoint.
   * Guards against concurrent calls and stopped state.
   * Transitions: buffering → transcribing → listening
   */
  async #transcribeBuffer() {
    if (this.#transcribing || this.#audioChunks.length === 0) return;

    this.#transcribing = true;
    this.#clearTimers();

    // Atomically drain the buffer so concurrent speech segments don't corrupt it
    const chunks = this.#audioChunks.splice(0);
    this.#setState('transcribing');
    this.#abortController = new AbortController();

    try {
      const wavBlob  = AmbientVAD.encodeWav(chunks, this.#sampleRate);
      const formData = new FormData();
      formData.append('audio', wavBlob, 'ambient.wav');
      formData.append('language', this._opts.language);

      const response = await fetch(this._opts.transcribeUrl, {
        method : 'POST',
        body   : formData,
        signal : this.#abortController.signal,
      });

      if (!response.ok) {
        throw new Error(
          `AmbientVAD: transcription HTTP ${response.status} ${response.statusText}`
        );
      }

      const data        = await response.json();
      const text        = data.text         ?? data.transcript   ?? '';
      const displayText = data.display_text ?? data.displayText  ?? text;

      if (text && !this.#stopped) {
        this._opts.onTranscript(text, displayText);
      }
    } catch (err) {
      if (err.name !== 'AbortError' && !this.#stopped) {
        this._opts.onError(err);
      }
    } finally {
      this.#transcribing    = false;
      this.#abortController = null;

      if (!this.#stopped) {
        this.#setState('listening');
        this.#startMaxBufferTimer();
      }
    }
  }
}

// ─── Exports ─────────────────────────────────────────────────────────────────

if (typeof globalThis !== 'undefined') {
  globalThis.AmbientVAD = AmbientVAD;
}

if (typeof module !== 'undefined' && typeof module.exports !== 'undefined') {
  module.exports = { AmbientVAD };
}

// ESM (uncomment when using with a bundler or native ESM):
// export { AmbientVAD };
