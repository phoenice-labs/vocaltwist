/**
 * content/content.js — VocalTwist Extension Main Content Script
 *
 * Entry point injected into every page. Bootstraps all sub-modules,
 * handles SPA navigation, and wires together focus-watcher, mic-button,
 * voice-orchestrator, and response-watcher.
 *
 * All sub-modules are loaded via manifest.json content_scripts array before
 * this file, so their globals are available on window.
 */

'use strict';

// Wrap in IIFE to isolate const declarations from other content scripts
// (all content scripts share the same top-level scope, so top-level `const`
// names must not collide with those declared in focus-watcher.js, etc.)
(function vtContentMain() {

// Guard against double-injection on the same page
if (window.__vtInitialized) {
  return; // already running, silently exit
}
window.__vtInitialized = true;

// Debug sentinel — visible to Playwright via the shared DOM
document.documentElement.setAttribute('data-vt-loaded', '1');

// ─── Sub-module aliases ───────────────────────────────────────────────────────
// (function-scoped, so they don't clash with top-level names in other scripts)

const focusWatcher    = window.__vtFocusWatcher;
const micBtn          = window.__vtMicButton;
const orchestrator    = window.__vtOrchestrator;
const respWatcher     = window.__vtResponseWatcher;

// ─── Helpers ──────────────────────────────────────────────────────────────────

function isSiteDisabled(settings) {
  const host = location.hostname.replace(/^www\./, '');
  return settings.disabledSites?.some((s) => s.replace(/^www\./, '') === host);
}

function getCustomSelectorForSite(settings) {
  const host = location.hostname.replace(/^www\./, '');
  return settings.customSelectors?.[host] ||
         settings.customSelectors?.[location.hostname] ||
         null;
}

// ─── Initialization ────────────────────────────────────────────────────────────

async function init() {
  // Load settings and backend status
  let settings, backendOnline;
  try {
    // Wrap sendMessage with a 3-second timeout — MV3 service worker wakeup can
    // cause the message to hang if the worker was just being started.
    const resp = await Promise.race([
      chrome.runtime.sendMessage({ type: MSG.GET_PROVIDER_STATUS }),
      new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 1000)),
    ]);
    backendOnline = resp?.backend ?? false;
    settings    = await new Promise((resolve) => {
      chrome.storage.sync.get(DEFAULTS, (s) => resolve({ ...DEFAULTS, ...s }));
    });
  } catch (_) {
    settings      = { ...DEFAULTS };
    backendOnline = false;
  }

  if (!settings.enabled) return;
  if (isSiteDisabled(settings)) return;

  // Initialize orchestrator
  orchestrator.init(settings, backendOnline);

  // Wire mic button click → toggle recording
  micBtn.setOnClick((currentState) => {
    if (currentState === 'recording') {
      orchestrator.stopRecording();
    } else if (currentState === 'idle' || currentState === 'error') {
      orchestrator.startRecording();
    }
  });

  // Wire focus events → mic button attach/detach
  focusWatcher.start(
    (inputEl) => {
      if (settings.showMicButton) {
        micBtn.attachTo(inputEl);
      }
      // In ambient mode, start VAD when user focuses an input
      if (settings.sttMode === 'ambient' && !orchestrator.isRecording) {
        startAmbientVAD(settings, backendOnline);
      }
    },
    () => {
      micBtn.detach();
      // Stop ambient VAD when focus leaves all inputs
      if (settings.sttMode === 'ambient') {
        chrome.runtime.sendMessage({ type: MSG.OFFSCREEN_VAD_STOP });
      }
    }
  );

  // Wire response watcher → TTS
  if (settings.ttsEnabled) {
    const customSelector = getCustomSelectorForSite(settings);
    await respWatcher.start((text) => {
      orchestrator.speak(text);
    }, customSelector);
  }

  // Start ambient VAD if already focused on a text input
  if (settings.sttMode === 'ambient') {
    const active = document.activeElement;
    if (active && focusWatcher.isTextInput(active)) {
      startAmbientVAD(settings, backendOnline);
    }
  }
}

// ─── Ambient VAD startup ──────────────────────────────────────────────────────

function startAmbientVAD(settings, backendOnline) {
  if (!backendOnline) return; // VAD requires the VocalTwist backend
  const backendUrl = (settings.backendUrl || DEFAULTS.backendUrl).replace(/\/$/, '');
  chrome.runtime.sendMessage({
    type:          MSG.OFFSCREEN_VAD_START,
    transcribeUrl: `${backendUrl}/transcribe`,
    language:      settings.language,
  });
}

// ─── SPA Navigation handling ──────────────────────────────────────────────────

let _lastUrl = location.href;

const navObserver = new MutationObserver(() => {
  if (location.href !== _lastUrl) {
    _lastUrl = location.href;
    setTimeout(reinitializeAll, SPA_REINIT_DELAY_MS ?? 750);
  }
});

navObserver.observe(document.body, { childList: true, subtree: true });

function reinitializeAll() {
  focusWatcher.stop();
  micBtn.detach();
  respWatcher.stop();
  init().catch(console.error);
}

// ─── Background message handler ───────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  switch (message.type) {
    case MSG.PROVIDER_CHANGED:
      orchestrator.setBackendStatus(message.backend);
      sendResponse({ ok: true });
      break;

    case MSG.SETTINGS_UPDATED: {
      const newSettings = message.settings;
      orchestrator.updateSettings(newSettings);

      // Re-wire response watcher if TTS settings changed
      if (newSettings.ttsEnabled !== undefined || newSettings.customSelectors !== undefined) {
        respWatcher.stop();
        const merged = { ...orchestrator.settings, ...newSettings };
        if (merged.ttsEnabled) {
          const custom = getCustomSelectorForSite(merged);
          respWatcher.start((text) => orchestrator.speak(text), custom);
        }
      }

      // Toggle mic button visibility
      if (newSettings.showMicButton === false) {
        micBtn.detach();
      }
      sendResponse({ ok: true });
      break;
    }

    case MSG.OFFSCREEN_AUDIO_READY:
      orchestrator.handleAudioBlob(message.buffer, message.mime);
      sendResponse({ ok: true });
      break;

    case MSG.OFFSCREEN_TRANSCRIPT: {
      // Transcript from ambient VAD
      const input = focusWatcher.currentInput();
      if (input) {
        micBtn.injectText(input, message.text);
      }
      sendResponse({ ok: true });
      break;
    }

    case MSG.STOP_SPEAKING:
      orchestrator.stopSpeaking();
      sendResponse({ ok: true });
      break;

    case 'TOGGLE_MIC':
      orchestrator.toggleRecording();
      sendResponse({ ok: true });
      break;

    default:
      sendResponse({ ok: false });
  }

  return true; // Keep channel open for async
});

// ─── Bootstrap ────────────────────────────────────────────────────────────────

init().catch(console.error);

})(); // end vtContentMain
