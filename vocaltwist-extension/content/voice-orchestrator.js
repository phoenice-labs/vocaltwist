/**
 * content/voice-orchestrator.js — STT/TTS Coordination
 *
 * Coordinates provider selection, recording lifecycle, TTS playback,
 * and the ambient VAD feedback-loop prevention.
 *
 * Provider switching happens automatically based on backend availability.
 *  - backendOnline=true  → VocalTwistSTT + VocalTwistTTS
 *  - backendOnline=false → NativeSTT + NativeTTS (Web Speech API)
 */

'use strict';

const voiceOrchestrator = (() => {
  let _settings     = null;
  let _backendOnline = false;
  let _sttProvider   = null;
  let _ttsProvider   = null;
  let _isSpeaking    = false;
  let _isRecording   = false;

  // ─── Provider factory ─────────────────────────────────────────────────────────

  function buildSTT(backendOnline, settings) {
    if (backendOnline && settings.backendUrl) {
      return new (window.__vtVocalTwistSTT)(settings.backendUrl, settings.apiKey);
    }
    return new (window.__vtNativeSTT)();
  }

  function buildTTS(backendOnline, settings) {
    if (backendOnline && settings.backendUrl) {
      return new (window.__vtVocalTwistTTS)(settings.backendUrl, settings.apiKey);
    }
    return new (window.__vtNativeTTS)();
  }

  // ─── Recording ────────────────────────────────────────────────────────────────

  async function startRecording() {
    if (_isRecording) return;
    stopSpeaking(); // Cancel any active TTS before recording

    _isRecording = true;
    window.__vtMicButton?.setState('recording');

    // For native STT, drive it entirely in-tab
    if (!_backendOnline) {
      const native = _sttProvider;
      native.start(
        _settings.language,
        (transcript, isFinal) => {
          if (transcript) {
            const input = window.__vtFocusWatcher?.currentInput();
            if (input) {
              window.__vtMicButton?.injectText(input, transcript);
            }
          }
          if (isFinal) {
            _isRecording = false;
            window.__vtMicButton?.setState('idle');
          }
        },
        (errMsg) => {
          _isRecording = false;
          window.__vtMicButton?.setState('error');
          console.warn('[VocalTwist] STT error:', errMsg);
          setTimeout(() => window.__vtMicButton?.setState('idle'), 3000);
        }
      );
    } else {
      // For VocalTwist backend, delegate recording to the offscreen document
      chrome.runtime.sendMessage({
        type:     MSG.START_RECORDING,
        language: _settings.language,
      });
    }
  }

  async function stopRecording() {
    if (!_isRecording) return;
    _isRecording = false;

    if (!_backendOnline) {
      _sttProvider?.stop();
      window.__vtMicButton?.setState('idle');
    } else {
      window.__vtMicButton?.setState('processing');
      chrome.runtime.sendMessage({ type: MSG.STOP_RECORDING });
    }
  }

  function toggleRecording() {
    if (_isRecording) {
      stopRecording();
    } else {
      startRecording();
    }
  }

  // ─── TTS ──────────────────────────────────────────────────────────────────────

  function speak(text) {
    if (!_settings?.ttsEnabled) return;
    if (!text?.trim()) return;

    _isSpeaking = true;

    // Pause ambient VAD to prevent feedback loop
    if (_settings.sttMode === 'ambient') {
      chrome.runtime.sendMessage({ type: MSG.OFFSCREEN_VAD_PAUSE });
    }

    _ttsProvider.speak(text, {
      voice:    _settings.voice,
      language: _settings.language,
      rate:     _settings.ttsSpeed,
      onEnd: () => {
        _isSpeaking = false;
        // Resume ambient VAD after brief buffer
        if (_settings.sttMode === 'ambient') {
          setTimeout(() => {
            chrome.runtime.sendMessage({ type: MSG.OFFSCREEN_VAD_RESUME });
          }, 500);
        }
      },
      onError: (err) => {
        _isSpeaking = false;
        console.warn('[VocalTwist] TTS error:', err);
      },
    });
  }

  function stopSpeaking() {
    if (!_isSpeaking) return;
    _ttsProvider?.stop();
    _isSpeaking = false;
    if (_settings?.sttMode === 'ambient') {
      chrome.runtime.sendMessage({ type: MSG.OFFSCREEN_VAD_RESUME });
    }
  }

  // ─── Provider switching ───────────────────────────────────────────────────────

  function setBackendStatus(online) {
    if (_backendOnline === online) return;
    _backendOnline = online;
    const wasRecording = _isRecording;
    const wasSpeaking  = _isSpeaking;

    stopRecording();
    stopSpeaking();

    _sttProvider = buildSTT(online, _settings);
    _ttsProvider = buildTTS(online, _settings);

    // Notify content script to update UI status
    document.dispatchEvent(new CustomEvent('vt:providerChanged', {
      detail: { backend: online }
    }));

    if (wasRecording) startRecording();
  }

  // ─── Init ─────────────────────────────────────────────────────────────────────

  function init(settings, backendOnline) {
    _settings      = { ...(DEFAULTS ?? {}), ...settings };
    _backendOnline = backendOnline ?? false;
    _sttProvider   = buildSTT(_backendOnline, _settings);
    _ttsProvider   = buildTTS(_backendOnline, _settings);
  }

  function updateSettings(newSettings) {
    const prevBackendUrl = _settings?.backendUrl;
    _settings = { ..._settings, ...newSettings };

    // Rebuild providers if backendUrl or apiKey changed
    if (
      newSettings.backendUrl !== prevBackendUrl ||
      newSettings.apiKey !== undefined
    ) {
      _sttProvider = buildSTT(_backendOnline, _settings);
      _ttsProvider = buildTTS(_backendOnline, _settings);
    }
  }

  // ─── Handle audio blob from offscreen ────────────────────────────────────────

  async function handleAudioBlob(buffer, mime) {
    try {
      const uint8 = new Uint8Array(buffer);
      const blob  = new Blob([uint8], { type: mime || 'audio/webm' });
      const text  = await _sttProvider.transcribe(blob, _settings.language);
      if (text) {
        const input = window.__vtFocusWatcher?.currentInput();
        if (input) {
          window.__vtMicButton?.injectText(input, text);
        }
      }
    } catch (err) {
      console.warn('[VocalTwist] Transcription error:', err.message);
      window.__vtMicButton?.setState('error');
      setTimeout(() => window.__vtMicButton?.setState('idle'), 3000);
    } finally {
      window.__vtMicButton?.setState('idle');
    }
  }

  return {
    init,
    updateSettings,
    startRecording,
    stopRecording,
    toggleRecording,
    speak,
    stopSpeaking,
    setBackendStatus,
    handleAudioBlob,
    get isRecording() { return _isRecording; },
    get isSpeaking()  { return _isSpeaking;  },
    get settings()    { return _settings;    },
    get backendOnline() { return _backendOnline; },
  };
})();

window.__vtOrchestrator = voiceOrchestrator;
