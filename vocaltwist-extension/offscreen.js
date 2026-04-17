/**
 * offscreen.js — Hidden audio document for MV3 microphone recording
 *
 * Owns getUserMedia and MediaRecorder (not available in service workers).
 * Communicates with background.js via chrome.runtime.sendMessage.
 *
 * Handles:
 *  - Push-to-talk recording via MediaRecorder
 *  - Ambient VAD via AmbientVAD (vendor/ambient-vad.js)
 */

'use strict';

// ─── State ────────────────────────────────────────────────────────────────────

let mediaRecorder  = null;
let recordedChunks = [];
let micStream      = null;
let ambientVAD     = null;
let vadPaused      = false;

// ─── Push-to-Talk Recording ───────────────────────────────────────────────────

async function startRecording() {
  try {
    micStream = await navigator.mediaDevices.getUserMedia({ audio: true, video: false });
  } catch (err) {
    chrome.runtime.sendMessage({
      type:  'OFFSCREEN_ERROR',
      error: `Microphone access denied: ${err.message}`,
    });
    return;
  }

  const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus')
    ? 'audio/webm;codecs=opus'
    : 'audio/webm';

  recordedChunks = [];
  mediaRecorder  = new MediaRecorder(micStream, { mimeType });

  mediaRecorder.ondataavailable = (e) => {
    if (e.data.size > 0) recordedChunks.push(e.data);
  };

  mediaRecorder.onstop = async () => {
    const blob   = new Blob(recordedChunks, { type: mimeType });
    const buffer = await blob.arrayBuffer();
    chrome.runtime.sendMessage({
      type:   MSG.OFFSCREEN_AUDIO_READY,
      buffer: Array.from(new Uint8Array(buffer)),
      mime:   mimeType,
    });
    stopStream();
  };

  mediaRecorder.start();
}

function stopRecording() {
  if (mediaRecorder?.state === 'recording') {
    mediaRecorder.stop();
  }
}

function stopStream() {
  if (micStream) {
    micStream.getTracks().forEach((t) => t.stop());
    micStream = null;
  }
}

// ─── Ambient VAD ─────────────────────────────────────────────────────────────

async function startAmbientVAD(transcribeUrl, language) {
  if (ambientVAD) {
    ambientVAD.stop();
    ambientVAD = null;
  }
  vadPaused = false;

  if (typeof AmbientVAD === 'undefined') {
    chrome.runtime.sendMessage({
      type:  'OFFSCREEN_ERROR',
      error: 'AmbientVAD not available — vad-web and onnxruntime-web not loaded',
    });
    return;
  }

  ambientVAD = new AmbientVAD({
    transcribeUrl,
    language:     language || 'en',
    silenceMs:    1500,
    maxBufferMs:  30_000,
    onTranscript: (text) => {
      if (!vadPaused) {
        chrome.runtime.sendMessage({ type: MSG.OFFSCREEN_TRANSCRIPT, text });
      }
    },
    onError: (err) => {
      chrome.runtime.sendMessage({ type: 'OFFSCREEN_ERROR', error: err.message });
    },
  });

  try {
    await ambientVAD.start();
  } catch (err) {
    chrome.runtime.sendMessage({ type: 'OFFSCREEN_ERROR', error: err.message });
  }
}

function stopAmbientVAD() {
  if (ambientVAD) {
    ambientVAD.stop();
    ambientVAD = null;
  }
  vadPaused = false;
}

// ─── Message Handling ─────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message) => {
  switch (message.type) {
    case MSG.OFFSCREEN_RECORD_START:
      startRecording();
      break;

    case MSG.OFFSCREEN_RECORD_STOP:
      stopRecording();
      break;

    case MSG.OFFSCREEN_VAD_START:
      startAmbientVAD(message.transcribeUrl, message.language);
      break;

    case MSG.OFFSCREEN_VAD_STOP:
      stopAmbientVAD();
      break;

    case MSG.OFFSCREEN_VAD_PAUSE:
      vadPaused = true;
      break;

    case MSG.OFFSCREEN_VAD_RESUME:
      vadPaused = false;
      break;
  }
});
