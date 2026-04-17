/**
 * providers/stt-vocaltwist.js — VocalTwist Whisper STT Provider
 *
 * Sends recorded audio blob to the VocalTwist backend (/transcribe).
 * Requires the VocalTwist server to be running locally.
 */

'use strict';

class VocalTwistSTTProvider {
  /**
   * @param {string} backendUrl  Base URL, e.g. 'http://localhost:8000'
   * @param {string} [apiKey]    Optional API key for X-Api-Key header
   */
  constructor(backendUrl, apiKey) {
    this._backendUrl = (backendUrl || 'http://localhost:8000').replace(/\/$/, '');
    this._apiKey     = apiKey || '';
  }

  /**
   * POST an audio blob/ArrayBuffer to /transcribe and return the transcript.
   * @param {Blob|ArrayBuffer|Uint8Array} audioData  Recorded audio
   * @param {string}                      [language] BCP-47 code
   * @returns {Promise<string>} Transcript text
   */
  async transcribe(audioData, language) {
    let blob;
    if (audioData instanceof Blob) {
      blob = audioData;
    } else {
      blob = new Blob([audioData], { type: 'audio/webm' });
    }

    const formData = new FormData();
    formData.append('audio', blob, 'recording.webm');

    const headers = {};
    if (this._apiKey) {
      headers['X-Api-Key'] = this._apiKey;
    }

    // Backend reads language as a URL query param (not a form field).
    // Normalize BCP-47 (hi-IN) → ISO 639-1 short code (hi) for Whisper.
    const url = new URL(`${this._backendUrl}/api/transcribe`);
    if (language) {
      const langCode = language.split('-')[0].toLowerCase();
      url.searchParams.set('language', langCode);
    }

    const res = await fetch(url.toString(), {
      method: 'POST',
      headers,
      body:   formData,
      signal: AbortSignal.timeout(30_000),
    });

    if (!res.ok) {
      throw new Error(`VocalTwist transcribe error: ${res.status} ${res.statusText}`);
    }

    const data = await res.json();
    return data.text ?? data.transcript ?? '';
  }
}

window.__vtVocalTwistSTT = VocalTwistSTTProvider;
