/**
 * shared/constants.js — Shared extension constants
 */

'use strict';

const DEFAULTS = {
  enabled:          true,
  backendUrl:       'http://localhost:8000',
  apiKey:           '',
  language:         'en-US',
  voice:            'auto',
  sttMode:          'push-to-talk',   // 'push-to-talk' | 'ambient'
  ttsEnabled:       true,
  disabledSites:    [],
  customSelectors:  {},
  ttsSpeed:         1.0,
  showMicButton:    true,
};

const BACKEND_PROBE_INTERVAL_MS = 30_000;
const BACKEND_PROBE_TIMEOUT_MS  = 2_000;
const TTS_DEBOUNCE_MS           = 1_500;  // Wait for streaming to settle
const SPA_REINIT_DELAY_MS       = 750;    // Let SPA DOM settle before reinit
const MIC_DETACH_GRACE_MS       = 200;    // Grace period before hiding mic button

if (typeof globalThis !== 'undefined') {
  globalThis.DEFAULTS                  = DEFAULTS;
  globalThis.BACKEND_PROBE_INTERVAL_MS = BACKEND_PROBE_INTERVAL_MS;
  globalThis.BACKEND_PROBE_TIMEOUT_MS  = BACKEND_PROBE_TIMEOUT_MS;
  globalThis.TTS_DEBOUNCE_MS           = TTS_DEBOUNCE_MS;
  globalThis.SPA_REINIT_DELAY_MS       = SPA_REINIT_DELAY_MS;
  globalThis.MIC_DETACH_GRACE_MS       = MIC_DETACH_GRACE_MS;
}

if (typeof module !== 'undefined' && typeof module.exports !== 'undefined') {
  module.exports = {
    DEFAULTS,
    BACKEND_PROBE_INTERVAL_MS,
    BACKEND_PROBE_TIMEOUT_MS,
    TTS_DEBOUNCE_MS,
    SPA_REINIT_DELAY_MS,
    MIC_DETACH_GRACE_MS,
  };
}
