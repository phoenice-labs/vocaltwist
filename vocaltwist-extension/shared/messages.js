/**
 * shared/messages.js — Chrome message type definitions
 * Single source of truth for all extension message types.
 */

'use strict';

const MSG = {
  // Background ↔ Content
  PROVIDER_CHANGED:        'PROVIDER_CHANGED',        // { backend: bool }
  SETTINGS_UPDATED:        'SETTINGS_UPDATED',        // { settings: object }

  // Content → Background
  START_RECORDING:         'START_RECORDING',         // { language: string }
  STOP_RECORDING:          'STOP_RECORDING',          // {}
  SPEAK_TEXT:              'SPEAK_TEXT',              // { text: string }
  STOP_SPEAKING:           'STOP_SPEAKING',           // {}
  GET_PROVIDER_STATUS:     'GET_PROVIDER_STATUS',     // {} → { backend: bool }

  // Background ↔ Offscreen
  OFFSCREEN_RECORD_START:  'OFFSCREEN_RECORD_START',  // { language: string }
  OFFSCREEN_RECORD_STOP:   'OFFSCREEN_RECORD_STOP',   // {}
  OFFSCREEN_AUDIO_READY:   'OFFSCREEN_AUDIO_READY',   // { buffer: ArrayBuffer }
  OFFSCREEN_VAD_PAUSE:     'OFFSCREEN_VAD_PAUSE',     // {}
  OFFSCREEN_VAD_RESUME:    'OFFSCREEN_VAD_RESUME',    // {}
  OFFSCREEN_VAD_START:     'OFFSCREEN_VAD_START',     // { language: string, transcribeUrl: string }
  OFFSCREEN_VAD_STOP:      'OFFSCREEN_VAD_STOP',      // {}
  OFFSCREEN_TRANSCRIPT:    'OFFSCREEN_TRANSCRIPT',    // { text: string }
  OFFSCREEN_ERROR:         'OFFSCREEN_ERROR',         // { error: string }
};

if (typeof globalThis !== 'undefined') {
  globalThis.MSG = MSG;
}

if (typeof module !== 'undefined' && typeof module.exports !== 'undefined') {
  module.exports = { MSG };
}
