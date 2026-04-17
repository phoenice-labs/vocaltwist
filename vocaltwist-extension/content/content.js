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

/** Direct content-script health check — bypasses the background SW race. */
async function checkBackendDirect(backendUrl) {
  try {
    const url = (backendUrl || DEFAULTS.backendUrl).replace(/\/$/, '');
    const res = await fetch(`${url}/api/health`, {
      signal: AbortSignal.timeout(3000),
    });
    return res.ok;
  } catch {
    return false;
  }
}

async function init() {
  // Load settings and backend status
  let settings, backendOnline;
  try {
    // Wrap sendMessage with a 5-second timeout — MV3 service worker wakeup
    // plus the live backend probe it runs when backendOnline=false can take
    // 2-3 seconds in Docker/WSL2 environments.
    const resp = await Promise.race([
      chrome.runtime.sendMessage({ type: MSG.GET_PROVIDER_STATUS }),
      new Promise((_, reject) => setTimeout(() => reject(new Error('timeout')), 5000)),
    ]);
    backendOnline = resp?.backend ?? false;
    settings    = await new Promise((resolve) => {
      chrome.storage.sync.get(DEFAULTS, (s) => resolve({ ...DEFAULTS, ...s }));
    });
  } catch (_) {
    settings      = { ...DEFAULTS };
    backendOnline = false;
  }

  // If the SW says offline, do a direct probe from the content script.
  // This covers the race where the SW's initial broadcast was lost.
  if (!backendOnline) {
    backendOnline = await checkBackendDirect(settings?.backendUrl);
  }

  if (!settings.enabled) return;
  if (isSiteDisabled(settings)) return;

  // Initialize orchestrator
  orchestrator.init(settings, backendOnline);

  // Expose current language for the test page (reads dataset.vtLanguage)
  document.documentElement.dataset.vtLanguage    = settings.language || 'en-US';
  document.documentElement.dataset.vtBackendOnline = String(backendOnline);

  console.log('[VocalTwist] init backendOnline=' + backendOnline + ' lang=' + settings.language);

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
    transcribeUrl: `${backendUrl}/api/transcribe`,
    language:      (settings.language || 'en').split('-')[0].toLowerCase(),
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
      document.documentElement.dataset.vtBackendOnline = String(message.backend);
      sendResponse({ ok: true });
      break;

    case MSG.SETTINGS_UPDATED: {
      const newSettings = message.settings;
      orchestrator.updateSettings(newSettings);

      // Keep DOM language attribute in sync so page JS can read current language
      if (newSettings.language) {
        document.documentElement.dataset.vtLanguage = newSettings.language;
      }

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

// ─── Storage-change listener (catches settings saved by popup) ────────────────
// chrome.storage.onChanged fires in the content script world directly — more
// reliable than waiting for the background SW to broadcast SETTINGS_UPDATED.

chrome.storage.onChanged.addListener((changes, area) => {
  if (area !== 'sync') return;

  const patch = {};
  for (const [key, { newValue }] of Object.entries(changes)) {
    patch[key] = newValue;
  }

  orchestrator.updateSettings(patch);

  if (patch.language) {
    document.documentElement.dataset.vtLanguage = patch.language;
    console.log('[VocalTwist] storage.onChanged language=' + patch.language);
  }

  // Re-wire response watcher if TTS settings changed
  if (patch.ttsEnabled !== undefined || patch.customSelectors !== undefined) {
    respWatcher.stop();
    const merged = { ...orchestrator.settings, ...patch };
    if (merged.ttsEnabled) {
      const custom = getCustomSelectorForSite(merged);
      respWatcher.start((text) => orchestrator.speak(text), custom);
    }
  }
});

// ─── Bootstrap ────────────────────────────────────────────────────────────────

init().catch(console.error);

})(); // end vtContentMain
