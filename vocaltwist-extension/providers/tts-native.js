/**
 * providers/tts-native.js — Browser speechSynthesis TTS Provider
 *
 * Wraps the Web Speech Synthesis API for text-to-speech playback.
 */

'use strict';

class NativeTTSProvider {
  #currentUtterance = null;

  /**
   * Speak text using the browser's built-in speech synthesis.
   * @param {string}   text
   * @param {object}   [opts]
   * @param {string}   [opts.voice]   Voice URI or partial name match
   * @param {number}   [opts.rate]    Playback speed (0.1–10, default 1.0)
   * @param {number}   [opts.pitch]   Pitch (0–2, default 1.0)
   * @param {string}   [opts.lang]    BCP-47 language tag
   * @param {Function} [opts.onEnd]   Called when speech completes
   * @param {Function} [opts.onError] Called on error
   */
  speak(text, { voice, rate, pitch, language, onEnd, onError } = {}) {
    this.stop();

    const utterance        = new SpeechSynthesisUtterance(text);
    utterance.rate         = rate  ?? 1.0;
    utterance.pitch        = pitch ?? 1.0;
    if (language) utterance.lang = language;

    // Select voice
    const selectedVoice = this.#selectVoice(voice, language);
    if (selectedVoice) utterance.voice = selectedVoice;

    utterance.onend   = () => onEnd?.();
    utterance.onerror = (e) => onError?.(e.error);

    this.#currentUtterance = utterance;
    speechSynthesis.speak(utterance);
  }

  stop() {
    if (speechSynthesis.speaking || speechSynthesis.pending) {
      speechSynthesis.cancel();
    }
    this.#currentUtterance = null;
  }

  /**
   * Pick the best available voice.
   * @param {string} [voiceNameOrUri]  Exact URI or partial name match
   * @param {string} [lang]            BCP-47 fallback filter
   * @returns {SpeechSynthesisVoice|null}
   */
  #selectVoice(voiceNameOrUri, language) {
    const voices = speechSynthesis.getVoices();
    if (!voices.length) return null;

    if (voiceNameOrUri && voiceNameOrUri !== 'auto') {
      const exact = voices.find(
        (v) => v.voiceURI === voiceNameOrUri || v.name === voiceNameOrUri
      );
      if (exact) return exact;
      const partial = voices.find((v) =>
        v.name.toLowerCase().includes(voiceNameOrUri.toLowerCase())
      );
      if (partial) return partial;
    }

    if (language) {
      const langMatch = voices.find((v) => v.lang.startsWith(language.split('-')[0]));
      if (langMatch) return langMatch;
    }

    return null;
  }

  /** @returns {SpeechSynthesisVoice[]} All available voices */
  getVoices() {
    return speechSynthesis.getVoices();
  }
}

window.__vtNativeTTS = NativeTTSProvider;
