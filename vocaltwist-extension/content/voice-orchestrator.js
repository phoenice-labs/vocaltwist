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
  let _processingTimeout = null;  // Safety timeout for processing state

  // Tracks input field state at recording start so interim transcripts
  // replace rather than append (prevents "hello hello there hello there" duplication)
  let _recordingBaseValue = '';
  let _recordingBaseInput = null;

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

  // ─── Transcript injection (replace-aware) ─────────────────────────────────────

  function _injectTranscript(input, transcript) {
    if (!input || !transcript) return;
    // Build the full new value: everything before recording started + new transcript
    const newVal = _recordingBaseValue
      ? `${_recordingBaseValue} ${transcript}`
      : transcript;

    if (input.isContentEditable) {
      // For contenteditable: use injectText which handles React etc.
      // We can't easily do "replace" so just append for now
      window.__vtMicButton?.injectText(input, transcript);
    } else {
      const proto  = input.tagName === 'TEXTAREA'
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
      if (setter) {
        setter.call(input, newVal);
      } else {
        input.value = newVal;
      }
      input.dispatchEvent(new Event('input',  { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  // ─── Recording ────────────────────────────────────────────────────────────────

  async function startRecording() {
    if (_isRecording) return;
    stopSpeaking(); // Cancel any active TTS before recording

    // Snapshot input value so interim results replace rather than append
    const currentInput    = window.__vtFocusWatcher?.currentInput();
    _recordingBaseInput   = currentInput;
    _recordingBaseValue   = currentInput?.value || '';

    _isRecording = true;
    window.__vtMicButton?.setState('recording');

    // For native STT, drive it entirely in-tab
    if (!_backendOnline) {
      // Track which provider + language so offline tests can verify via DOM
      document.documentElement.dataset.vtLastSttProvider = 'native';
      document.documentElement.dataset.vtLastSttLanguage = _settings?.language || '';
      document.documentElement.dataset.vtLastSttTs       = Date.now().toString();

      const native = _sttProvider;
      native.start(
        _settings.language,
        (transcript, isFinal) => {
          if (transcript) {
            const input = window.__vtFocusWatcher?.currentInput() || _recordingBaseInput;
            if (input) {
              _injectTranscript(input, transcript);
            }
          }
          if (isFinal) {
            _recordingBaseValue = '';
            _recordingBaseInput = null;
            _isRecording = false;
            window.__vtMicButton?.setState('idle');
          }
        },
        (errMsg) => {
          _recordingBaseValue = '';
          _recordingBaseInput = null;
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
      }).catch(e => console.warn('[VocalTwist] START_RECORDING error:', e.message));
    }
  }

  async function stopRecording() {
    if (!_isRecording) return;
    _isRecording = false;

    // Clear safety timeout — we got a proper stop/audio response
    if (_processingTimeout) {
      clearTimeout(_processingTimeout);
      _processingTimeout = null;
    }

    if (!_backendOnline) {
      _sttProvider?.stop();
      window.__vtMicButton?.setState('idle');
    } else {
      window.__vtMicButton?.setState('processing');
      // Safety timeout: if OFFSCREEN_AUDIO_READY never arrives, auto-reset
      _processingTimeout = setTimeout(() => {
        console.warn('[VocalTwist] Safety timeout: mic reset from processing to idle');
        window.__vtMicButton?.setState('idle');
        _processingTimeout = null;
      }, 15_000);
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

    const providerName = _backendOnline ? 'vocaltwist' : 'native';
    // Write to DOM so Playwright (main world) can observe which provider fired
    document.documentElement.dataset.vtLastSpeakProvider = providerName;
    document.documentElement.dataset.vtLastSpeakLang     = _settings?.language || '';
    document.documentElement.dataset.vtLastSpeakVoice    = _settings?.voice    || '';
    document.documentElement.dataset.vtLastSpeakTs       = Date.now().toString();

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

  // ─── Error recovery ───────────────────────────────────────────────────────────

  function handleRecordingError(errMsg) {
    console.warn('[VocalTwist] Recording error:', errMsg);

    // Clear safety timeout — the error IS our signal
    if (_processingTimeout) {
      clearTimeout(_processingTimeout);
      _processingTimeout = null;
    }

    _isRecording        = false;
    _recordingBaseValue = '';
    _recordingBaseInput = null;

    window.__vtMicButton?.setState('error');
    setTimeout(() => window.__vtMicButton?.setState('idle'), 3000);
  }

  // ─── Handle audio blob from offscreen ────────────────────────────────────────

  async function handleAudioBlob(buffer, mime) {
    // Clear safety timeout — audio arrived, so we're actively processing
    if (_processingTimeout) {
      clearTimeout(_processingTimeout);
      _processingTimeout = null;
    }

    // Track provider + language for test observability
    document.documentElement.dataset.vtLastSttProvider = 'vocaltwist';
    document.documentElement.dataset.vtLastSttLanguage = _settings?.language || '';
    document.documentElement.dataset.vtLastSttTs       = Date.now().toString();

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
    handleRecordingError,
    get isRecording() { return _isRecording; },
    get isSpeaking()  { return _isSpeaking;  },
    get settings()    { return _settings;    },
    get backendOnline() { return _backendOnline; },
  };
})();

window.__vtOrchestrator = voiceOrchestrator;
