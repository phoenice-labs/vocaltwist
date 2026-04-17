/**
 * providers/stt-native.js — Browser Web Speech API STT Provider
 *
 * Wraps webkitSpeechRecognition for push-to-talk dictation.
 *
 * Note: Web Speech API in Chrome sends audio to Google's servers by default.
 * Users who need true privacy should run VocalTwist locally (Whisper STT).
 */

'use strict';

class NativeSTTProvider {
  #recognition = null;
  #onResult     = null;
  #onError      = null;
  #started      = false;

  /** @returns {boolean} True when the browser supports Web Speech API */
  static isSupported() {
    return typeof webkitSpeechRecognition !== 'undefined' || typeof SpeechRecognition !== 'undefined';
  }

  /**
   * Start speech recognition.
   * @param {string}   language  BCP-47 language tag, e.g. 'en-US'
   * @param {Function} onResult  (transcript: string, isFinal: boolean) => void
   * @param {Function} onError   (error: string) => void
   */
  start(language, onResult, onError) {
    if (this.#started) return;

    const SpeechRecognitionClass =
      typeof webkitSpeechRecognition !== 'undefined'
        ? webkitSpeechRecognition
        : SpeechRecognition;

    this.#recognition      = new SpeechRecognitionClass();
    this.#onResult         = onResult;
    this.#onError          = onError;
    this.#recognition.continuous      = false;
    this.#recognition.interimResults  = true;
    this.#recognition.lang            = language || 'en-US';

    this.#recognition.onresult = (e) => {
      const transcript = Array.from(e.results)
        .map((r) => r[0].transcript)
        .join('');
      const isFinal = e.results[e.results.length - 1].isFinal;
      this.#onResult?.(transcript, isFinal);
    };

    this.#recognition.onerror = (e) => {
      this.#started = false;
      this.#onError?.(e.error);
    };

    this.#recognition.onend = () => {
      this.#started = false;
    };

    try {
      this.#recognition.start();
      this.#started = true;
    } catch (err) {
      this.#started = false;
      onError?.(err.message);
    }
  }

  stop() {
    if (this.#recognition) {
      try { this.#recognition.stop(); } catch (_) { /* best-effort */ }
      this.#started = false;
    }
  }

  get isRecording() {
    return this.#started;
  }
}

window.__vtNativeSTT = NativeSTTProvider;
