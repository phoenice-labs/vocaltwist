/**
 * popup/popup.js — VocalTwist Settings Popup Logic
 *
 * Loads settings from chrome.storage.sync, populates UI,
 * handles save, test connection, and per-site disable.
 */

'use strict';

const DEFAULTS_POPUP = {
  enabled:         true,
  backendUrl:      'http://localhost:8000',
  apiKey:          '',
  language:        'en-US',
  voice:           'auto',
  sttMode:         'push-to-talk',
  ttsEnabled:      true,
  disabledSites:   [],
  customSelectors: {},
  ttsSpeed:        1.0,
  showMicButton:   true,
};

// ─── DOM references ───────────────────────────────────────────────────────────

const $  = (id) => document.getElementById(id);
const masterToggle  = $('masterToggle');
const statusDot     = $('statusDot');
const statusLabel   = $('statusLabel');
const modePTT       = $('modePTT');
const modeAmbient   = $('modeAmbient');
const languageSelect = $('languageSelect');
const voiceSelect   = $('voiceSelect');
const ttsEnabled    = $('ttsEnabled');
const showMicButton = $('showMicButton');
const disableOnSite = $('disableOnSite');
const backendUrl    = $('backendUrl');
const apiKey        = $('apiKey');
const ttsSpeed      = $('ttsSpeed');
const ttsSpeedVal   = $('ttsSpeedVal');
const customSelector = $('customSelector');
const testBtn       = $('testConnection');
const testResult    = $('testResult');
const saveBtn       = $('saveBtn');

// ─── Current tab hostname ─────────────────────────────────────────────────────

let currentHostname = '';

async function getCurrentHostname() {
  return new Promise((resolve) => {
    chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
      try {
        const url = new URL(tabs[0]?.url || '');
        resolve(url.hostname.replace(/^www\./, ''));
      } catch {
        resolve('');
      }
    });
  });
}

// ─── Load & populate settings ─────────────────────────────────────────────────

async function loadSettings() {
  const settings = await new Promise((resolve) => {
    chrome.storage.sync.get(DEFAULTS_POPUP, (s) => resolve({ ...DEFAULTS_POPUP, ...s }));
  });

  masterToggle.checked          = settings.enabled;
  modePTT.checked               = settings.sttMode !== 'ambient';
  modeAmbient.checked           = settings.sttMode === 'ambient';
  languageSelect.value          = settings.language;
  ttsEnabled.checked            = settings.ttsEnabled;
  showMicButton.checked         = settings.showMicButton;
  backendUrl.value              = settings.backendUrl;
  apiKey.value                  = settings.apiKey || '';
  ttsSpeed.value                = settings.ttsSpeed;
  ttsSpeedVal.textContent       = `${parseFloat(settings.ttsSpeed).toFixed(1)}×`;

  // Disable on this site
  const disabled = (settings.disabledSites || []).includes(currentHostname);
  disableOnSite.checked = disabled;

  // Custom selector for this site
  customSelector.value = settings.customSelectors?.[currentHostname] || '';

  // Populate voices
  populateVoices(settings.voice);

  return settings;
}

// ─── Voice dropdown ───────────────────────────────────────────────────────────

function populateVoices(selectedVoice) {
  const voices = speechSynthesis.getVoices();
  // Clear existing options except 'auto'
  voiceSelect.innerHTML = '<option value="auto">Auto</option>';
  voices.forEach((v) => {
    const opt   = document.createElement('option');
    opt.value   = v.voiceURI;
    opt.textContent = `${v.name} (${v.lang})`;
    voiceSelect.appendChild(opt);
  });
  voiceSelect.value = selectedVoice || 'auto';
}

speechSynthesis.onvoiceschanged = () => populateVoices(voiceSelect.value);

// ─── Provider status ──────────────────────────────────────────────────────────

async function updateProviderStatus() {
  try {
    const resp = await chrome.runtime.sendMessage({ type: 'GET_PROVIDER_STATUS' });
    if (resp?.backend) {
      statusDot.className = 'status-dot online';
      const url = backendUrl.value || 'localhost:8000';
      statusLabel.textContent = `🟢 VocalTwist (High Quality) · ${url} · Connected`;
    } else {
      statusDot.className = 'status-dot offline';
      statusLabel.textContent = '🔵 Browser built-in (Web Speech API)';
    }
  } catch {
    statusDot.className = 'status-dot error';
    statusLabel.textContent = '⚠️ Extension error — please reload';
  }
}

// ─── Test connection ──────────────────────────────────────────────────────────

testBtn.addEventListener('click', async () => {
  testResult.textContent = 'Testing…';
  testResult.style.color = '#718096';
  const url = (backendUrl.value || 'http://localhost:8000').replace(/\/$/, '');
  try {
    const res = await fetch(`${url}/health`, { signal: AbortSignal.timeout(3000) });
    if (res.ok) {
      testResult.textContent = '✅ Connected';
      testResult.style.color = '#2f855a';
    } else {
      testResult.textContent = `❌ HTTP ${res.status}`;
      testResult.style.color = '#c53030';
    }
  } catch {
    testResult.textContent = '❌ Unreachable';
    testResult.style.color = '#c53030';
  }
});

// ─── Speed slider live display ────────────────────────────────────────────────

ttsSpeed.addEventListener('input', () => {
  ttsSpeedVal.textContent = `${parseFloat(ttsSpeed.value).toFixed(1)}×`;
});

// ─── Save settings ────────────────────────────────────────────────────────────

saveBtn.addEventListener('click', async () => {
  saveBtn.textContent    = 'Saving…';
  saveBtn.disabled       = true;

  const currentSettings  = await new Promise((resolve) => {
    chrome.storage.sync.get(DEFAULTS_POPUP, (s) => resolve({ ...DEFAULTS_POPUP, ...s }));
  });

  // Update disabledSites list
  let disabledSites = [...(currentSettings.disabledSites || [])];
  if (disableOnSite.checked && currentHostname && !disabledSites.includes(currentHostname)) {
    disabledSites.push(currentHostname);
  } else if (!disableOnSite.checked) {
    disabledSites = disabledSites.filter((s) => s !== currentHostname);
  }

  // Update customSelectors
  const customSelectors = { ...(currentSettings.customSelectors || {}) };
  const selectorVal = customSelector.value.trim();
  if (selectorVal && currentHostname) {
    customSelectors[currentHostname] = selectorVal;
  } else if (!selectorVal && currentHostname) {
    delete customSelectors[currentHostname];
  }

  const newSettings = {
    enabled:         masterToggle.checked,
    backendUrl:      backendUrl.value.trim() || 'http://localhost:8000',
    apiKey:          apiKey.value.trim(),
    language:        languageSelect.value,
    voice:           voiceSelect.value,
    sttMode:         modeAmbient.checked ? 'ambient' : 'push-to-talk',
    ttsEnabled:      ttsEnabled.checked,
    showMicButton:   showMicButton.checked,
    ttsSpeed:        parseFloat(ttsSpeed.value),
    disabledSites,
    customSelectors,
  };

  // Save directly to storage (reliable — no service worker round-trip)
  await new Promise((resolve) => chrome.storage.sync.set(newSettings, resolve));

  // Notify background to update its in-memory state and broadcast to tabs
  // Fire-and-forget — don't await; save is already done above
  chrome.runtime.sendMessage({ type: 'SETTINGS_UPDATED', settings: newSettings }).catch(() => {});

  saveBtn.textContent = '✅ Saved';
  setTimeout(() => {
    saveBtn.textContent = 'Save';
    saveBtn.disabled    = false;
  }, 1500);

  updateProviderStatus();
});

// ─── Init ─────────────────────────────────────────────────────────────────────

(async () => {
  currentHostname = await getCurrentHostname();
  await loadSettings();
  await updateProviderStatus();
})();
