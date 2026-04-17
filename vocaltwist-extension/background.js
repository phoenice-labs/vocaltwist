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

let backendOnline   = false;
let offscreenActive = false;

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
  const settings = await getSettings();
  const url      = (settings.backendUrl || DEFAULTS.backendUrl).replace(/\/$/, '');

  try {
    const res     = await fetch(`${url}/api/health`, { signal: AbortSignal.timeout(BACKEND_PROBE_TIMEOUT_MS) });
    const wasOnline = backendOnline;
    backendOnline   = res.ok;
    if (wasOnline !== backendOnline) {
      broadcastToAllTabs({ type: MSG.PROVIDER_CHANGED, backend: backendOnline });
      updateBadge();
    }
  } catch {
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
      sendResponse({ backend: backendOnline });
      break;

    case MSG.START_RECORDING: {
      await ensureOffscreenDocument();
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
      // Relay transcription result back to the tab that started recording
      const tabs = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
      if (tabs[0]?.id) {
        chrome.tabs.sendMessage(tabs[0].id, {
          type: MSG.OFFSCREEN_AUDIO_READY,
          buffer: message.buffer,
          backendOnline,
        }).catch(() => {});
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

// Run probe immediately and then on interval
probeBackend();
setInterval(probeBackend, BACKEND_PROBE_INTERVAL_MS);
