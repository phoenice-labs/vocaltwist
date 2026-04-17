/**
 * providers/tts-vocaltwist.js — VocalTwist Edge Neural TTS Provider
 *
 * POSTs text to VocalTwist backend (/speak) and plays audio via AudioContext.
 * Provides higher quality neural voices compared to browser speechSynthesis.
 */

'use strict';

class VocalTwistTTSProvider {
  #audioCtx     = null;
  #currentSource = null;

  /**
   * @param {string} backendUrl  Base URL, e.g. 'http://localhost:8000'
   * @param {string} [apiKey]    Optional API key
   */
  constructor(backendUrl, apiKey) {
    this._backendUrl = (backendUrl || 'http://localhost:8000').replace(/\/$/, '');
    this._apiKey     = apiKey || '';
  }

  /**
   * Speak text via VocalTwist's Edge Neural TTS.
   * @param {string}   text
   * @param {object}   [opts]
   * @param {string}   [opts.voice]    Voice identifier
   * @param {string}   [opts.language] BCP-47 language tag
   * @param {number}   [opts.rate]     Speaking rate (0.5–2.0)
   * @param {Function} [opts.onEnd]    Called when playback completes
   * @param {Function} [opts.onError]  Called on error
   */
  async speak(text, { voice, language, rate, onEnd, onError } = {}) {
    this.stop();

    console.log('[VocalTwist] tts-vocaltwist.speak()', { language, voice, backendUrl: this._backendUrl });

    const headers = { 'Content-Type': 'application/json' };
    if (this._apiKey) headers['X-Api-Key'] = this._apiKey;

    // Normalize BCP-47 (hi-IN) → ISO 639-1 short code (hi) for voice lookup.
    const langCode = language ? language.split('-')[0].toLowerCase() : undefined;

    // Only send edge-tts compatible voice names to the backend.
    // Browser Web Speech API voice URIs (e.g. "Google हिन्दी (hi-IN)") are
    // incompatible with edge-tts and cause a 500 error — filter them out and
    // let the backend choose the correct voice from the language code instead.
    const edgeVoice = (voice && voice !== 'auto' && /Neural$/i.test(voice))
      ? voice
      : undefined;

    let res;
    try {
      res = await fetch(`${this._backendUrl}/api/speak`, {
        method:  'POST',
        headers,
        body:    JSON.stringify({ text, voice: edgeVoice, language: langCode, rate }),
        signal:  AbortSignal.timeout(30_000),
      });
    } catch (err) {
      onError?.(err.message);
      return;
    }

    if (!res.ok) {
      onError?.(`VocalTwist TTS error: ${res.status} ${res.statusText}`);
      return;
    }

    let audioBuffer;
    try {
      const arrayBuffer = await res.arrayBuffer();
      if (!this.#audioCtx || this.#audioCtx.state === 'closed') {
        this.#audioCtx = new AudioContext();
      }
      audioBuffer = await this.#audioCtx.decodeAudioData(arrayBuffer);
    } catch (err) {
      onError?.(err.message);
      return;
    }

    const source    = this.#audioCtx.createBufferSource();
    source.buffer   = audioBuffer;
    source.connect(this.#audioCtx.destination);
    source.onended  = () => {
      this.#currentSource = null;
      onEnd?.();
    };
    this.#currentSource = source;
    source.start();
  }

  stop() {
    if (this.#currentSource) {
      try { this.#currentSource.stop(); } catch (_) { /* already ended */ }
      this.#currentSource = null;
    }
  }
}

window.__vtVocalTwistTTS = VocalTwistTTSProvider;
