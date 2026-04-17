/**
 * background.js — VocalTwist Extension Service Worker
 *
 * Responsibilities:
 *  - Settings store (chrome.storage.sync)
 *  - Backend health probe every 30s, broadcasts PROVIDER_CHANGED to all tabs
 *  - Offscreen document lifecycle (creates/destroys for audio recording)
 *  - Message routing between content scripts and providers
 *  - Keyboard command handling
 */

import { MSG } from './shared/messages.js';
import { DEFAULTS, BACKEND_PROBE_INTERVAL_MS, BACKEND_PROBE_TIMEOUT_MS } from './shared/constants.js';

// ─── State ────────────────────────────────────────────────────────────────────

let backendOnline      = false;
let offscreenActive    = false;
let recordingTabId     = null;   // Tab that initiated the last START_RECORDING
// Monotonically-increasing probe generation counter.
// When a newer probe starts, older in-flight probes see their generation is
// stale and discard their results — prevents a slow "old-URL" probe from
// overwriting the result of a faster "new-URL" probe.
let probeGeneration    = 0;

// ─── Settings ─────────────────────────────────────────────────────────────────

async function getSettings() {
  return new Promise((resolve) => {
    chrome.storage.sync.get(DEFAULTS, (stored) => resolve({ ...DEFAULTS, ...stored }));
  });
}

async function saveSettings(partial) {
  return new Promise((resolve) => {
    chrome.storage.sync.set(partial, resolve);
  });
}

// ─── Backend Health Probe ─────────────────────────────────────────────────────

async function probeBackend() {
  const myGen = ++probeGeneration;   // Claim a generation slot before any await
  const settings = await getSettings();
  const url      = (settings.backendUrl || DEFAULTS.backendUrl).replace(/\/$/, '');

  try {
    const res     = await fetch(`${url}/api/health`, { signal: AbortSignal.timeout(BACKEND_PROBE_TIMEOUT_MS) });
    if (myGen !== probeGeneration) return;   // A newer probe superseded us — discard
    const wasOnline = backendOnline;
    backendOnline   = res.ok;
    if (wasOnline !== backendOnline) {
      broadcastToAllTabs({ type: MSG.PROVIDER_CHANGED, backend: backendOnline });
      updateBadge();
    }
  } catch {
    if (myGen !== probeGeneration) return;   // A newer probe superseded us — discard
    if (backendOnline) {
      backendOnline = false;
      broadcastToAllTabs({ type: MSG.PROVIDER_CHANGED, backend: false });
      updateBadge();
    }
  }
}

// ─── Badge ────────────────────────────────────────────────────────────────────

function updateBadge(recording = false) {
  if (recording) {
    chrome.action.setBadgeText({ text: '●' });
    chrome.action.setBadgeBackgroundColor({ color: '#e53e3e' });
  } else {
    chrome.action.setBadgeText({ text: '' });
  }
}

// ─── Offscreen Document ───────────────────────────────────────────────────────

async function ensureOffscreenDocument() {
  if (offscreenActive) return;
  const contexts = await chrome.offscreen.getContexts?.() ?? [];
  if (contexts.length > 0) {
    offscreenActive = true;
    return;
  }
  await chrome.offscreen.createDocument({
    url:    chrome.runtime.getURL('offscreen.html'),
    reasons: ['USER_MEDIA'],
    justification: 'Microphone recording for VocalTwist voice input',
  });
  offscreenActive = true;
}

async function closeOffscreenDocument() {
  if (!offscreenActive) return;
  try {
    await chrome.offscreen.closeDocument();
  } catch (_) { /* already closed */ }
  offscreenActive = false;
}

// ─── Tab Broadcasting ─────────────────────────────────────────────────────────

async function broadcastToAllTabs(message) {
  const tabs = await chrome.tabs.query({});
  for (const tab of tabs) {
    if (tab.id) {
      chrome.tabs.sendMessage(tab.id, message).catch(() => {
        // Tab may not have our content script — ignore
      });
    }
  }
}

// ─── Message Handling ─────────────────────────────────────────────────────────

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  handleMessage(message, sender, sendResponse);
  return true; // Keep channel open for async responses
});

async function handleMessage(message, sender, sendResponse) {
  const settings = await getSettings();

  switch (message.type) {
    case MSG.GET_PROVIDER_STATUS:
      // Run a fresh probe when we think the backend is offline — avoids the
      // race where the initial probe fires before any tabs are open and the
      // PROVIDER_CHANGED broadcast is lost.
      if (!backendOnline) {
        await probeBackend();
      }
      sendResponse({ backend: backendOnline });
      break;

    case MSG.START_RECORDING: {
      await ensureOffscreenDocument();
      // Remember which tab requested recording so we can relay the audio back
      recordingTabId = sender?.tab?.id ?? null;
      updateBadge(true);
      chrome.runtime.sendMessage({
        type:         MSG.OFFSCREEN_RECORD_START,
        language:     message.language || settings.language,
        backendUrl:   settings.backendUrl,
        apiKey:       settings.apiKey,
        backendOnline,
      });
      sendResponse({ ok: true });
      break;
    }

    case MSG.STOP_RECORDING: {
      chrome.runtime.sendMessage({ type: MSG.OFFSCREEN_RECORD_STOP });
      updateBadge(false);
      sendResponse({ ok: true });
      break;
    }

    case MSG.SPEAK_TEXT: {
      // TTS is handled in content script; background just routes the stop
      sendResponse({ ok: true });
      break;
    }

    case MSG.STOP_SPEAKING: {
      broadcastToAllTabs({ type: MSG.STOP_SPEAKING });
      sendResponse({ ok: true });
      break;
    }

    case MSG.OFFSCREEN_AUDIO_READY: {
      // Relay audio back to the tab that started recording.
      // Fall back to active tab if recordingTabId was not captured.
      let targetTabId = recordingTabId;
      if (!targetTabId) {
        const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
        targetTabId = tabs[0]?.id ?? null;
      }
      console.log('[VT BG] OFFSCREEN_AUDIO_READY → relaying to tabId=' + targetTabId);
      recordingTabId = null;
      if (targetTabId) {
        chrome.tabs.sendMessage(targetTabId, {
          type:   MSG.OFFSCREEN_AUDIO_READY,
          buffer: message.buffer,
          mime:   message.mime,
          backendOnline,
        }).catch(e => console.warn('[VT BG] relay OFFSCREEN_AUDIO_READY error:', e.message));
      }
      break;
    }

    case MSG.OFFSCREEN_TRANSCRIPT: {
      // Relay VAD transcript to the active tab
      const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      if (tabs[0]?.id) {
        chrome.tabs.sendMessage(tabs[0].id, {
          type: MSG.OFFSCREEN_TRANSCRIPT,
          text: message.text,
        }).catch(() => {});
      }
      break;
    }

    case MSG.SETTINGS_UPDATED: {
      await saveSettings(message.settings);
      broadcastToAllTabs({ type: MSG.SETTINGS_UPDATED, settings: message.settings });
      sendResponse({ ok: true });
      // Re-probe immediately when backendUrl changes
      if (message.settings.backendUrl !== undefined) {
        probeBackend();
      }
      break;
    }

    case MSG.OFFSCREEN_ERROR: {
      // Offscreen doc failed (e.g. getUserMedia denied or no recorder active).
      // Relay the error to the tab that triggered recording so it can reset.
      console.warn('[VT BG] OFFSCREEN_ERROR:', message.error);
      updateBadge(false);
      let targetTabId = recordingTabId;
      recordingTabId  = null;
      if (!targetTabId) {
        const tabs  = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
        targetTabId = tabs[0]?.id ?? null;
      }
      if (targetTabId) {
        chrome.tabs.sendMessage(targetTabId, {
          type:  MSG.OFFSCREEN_ERROR,
          error: message.error,
        }).catch(() => {});
      }
      sendResponse({ ok: true });
      break;
    }

    default:
      sendResponse({ ok: false, error: 'Unknown message type' });
  }
}

// ─── Command Handling ─────────────────────────────────────────────────────────

chrome.commands.onCommand.addListener(async (command) => {
  if (command === 'toggle-mic') {
    const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
    if (tabs[0]?.id) {
      chrome.tabs.sendMessage(tabs[0].id, { type: 'TOGGLE_MIC' }).catch(() => {});
    }
  }
});

// ─── Install / Startup ────────────────────────────────────────────────────────

chrome.runtime.onInstalled.addListener(async (details) => {
  if (details.reason === 'install') {
    // Set defaults on fresh install
    await saveSettings(DEFAULTS);
    // Open onboarding tab
    chrome.tabs.create({ url: chrome.runtime.getURL('popup/onboarding.html') });
  }
  probeBackend();
});

chrome.runtime.onStartup.addListener(() => {
  probeBackend();
});

// Re-probe immediately when backendUrl changes in storage (e.g. from test fixtures
// or users typing a new URL in the popup).  This avoids a 30-second wait for the
// next scheduled probe interval.
chrome.storage.onChanged.addListener((changes, area) => {
  if (area === 'sync' && 'backendUrl' in changes) {
    probeBackend();
  }
});

// Run probe immediately and then on interval
probeBackend();
setInterval(probeBackend, BACKEND_PROBE_INTERVAL_MS);
