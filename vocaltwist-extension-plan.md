# VocalTwist Chrome Extension — Full Build Plan

> **TL;DR:** A fully self-hosted, bidirectional voice layer for any web app. Speak into any text field. Hear AI responses read back. Zero cloud. Works for everyone out of the box, with Whisper-quality upgrade for power users running VocalTwist locally.

---

## Table of Contents

1. [Architecture Decision](#1-architecture-decision)
2. [Repository Structure](#2-repository-structure)
3. [Core Components Deep Dive](#3-core-components-deep-dive)
4. [Provider System](#4-provider-system)
5. [AI Response Detection Strategy](#5-ai-response-detection-strategy)
6. [Phase-by-Phase Build Plan](#6-phase-by-phase-build-plan)
7. [Manifest & Permissions](#7-manifest--permissions)
8. [Key Technical Patterns](#8-key-technical-patterns)
9. [Popup UI Spec](#9-popup-ui-spec)
10. [Testing Strategy](#10-testing-strategy)
11. [Chrome Web Store Launch Checklist](#11-chrome-web-store-launch-checklist)
12. [What You Reuse from VocalTwist](#12-what-you-reuse-from-vocaltwist)

---

## 1. Architecture Decision

### Chosen: Hybrid Provider Model

```
┌─────────────────────────────────────────────────────────┐
│                    Chrome Extension                      │
│                                                         │
│  ┌───────────┐    ┌──────────────┐    ┌─────────────┐  │
│  │  Content   │    │  Background  │    │   Offscreen  │  │
│  │  Script   │◄──►│   Worker     │◄──►│  Document   │  │
│  │           │    │              │    │  (audio)    │  │
│  └───────────┘    └──────┬───────┘    └─────────────┘  │
│                          │                              │
└──────────────────────────┼──────────────────────────────┘
                           │
              ┌────────────┴────────────┐
              │                         │
     ┌────────▼────────┐      ┌─────────▼────────┐
     │  Browser Native  │      │ VocalTwist Backend│
     │  (always works)  │      │ (quality upgrade) │
     │                  │      │                   │
     │ webkitSpeech     │      │ Whisper STT       │
     │ Recognition      │      │ Edge Neural TTS   │
     │ speechSynthesis  │      │ Silero VAD        │
     └──────────────────┘      └───────────────────┘
          🔵 Standard                🟢 High Quality
```

**Why this approach:**
- Non-technical users install and go — zero setup required
- Power users running VocalTwist locally get Whisper accuracy + Neural TTS automatically
- Your existing VocalTwist backend needs zero changes
- Natural upgrade/upsell story built into the product

**Provider switching logic:**
```
On startup → ping localhost:8000/health
  ✓ Success  → use VocalTwist (Whisper STT + Edge Neural TTS)
  ✗ Fail     → use Browser Native (Web Speech API + speechSynthesis)

Re-probe every 30s → switch providers seamlessly, no user action needed
```

---

## 2. Repository Structure

```
vocaltwist-extension/
│
├── manifest.json                  # MV3 config
├── background.js                  # Service worker
├── offscreen.html                 # Hidden audio page
├── offscreen.js                   # Microphone owner + VAD
│
├── content/
│   ├── content.js                 # Main injected script
│   ├── content.css                # Floating UI styles
│   ├── focus-watcher.js           # Detects active text inputs
│   ├── mic-button.js              # Floating mic button UI
│   ├── response-watcher.js        # MutationObserver for AI replies
│   └── voice-orchestrator.js      # Coordinates STT/TTS, swaps providers
│
├── providers/
│   ├── stt-native.js              # webkitSpeechRecognition wrapper
│   ├── stt-vocaltwist.js          # POST to localhost:8000/transcribe
│   ├── tts-native.js              # speechSynthesis wrapper
│   └── tts-vocaltwist.js          # POST to localhost:8000/speak + stream
│
├── popup/
│   ├── popup.html                 # Settings UI
│   ├── popup.js                   # Settings logic
│   └── popup.css                  # Settings styles
│
├── selectors/
│   └── site-registry.json         # Known AI app selectors
│
├── shared/
│   ├── constants.js               # Shared constants
│   └── messages.js                # Chrome message type definitions
│
├── vendor/
│   └── ambient-vad.js             # Ported from VocalTwist frontend/
│
└── icons/
    ├── icon16.png
    ├── icon32.png
    ├── icon48.png
    └── icon128.png
```

---

## 3. Core Components Deep Dive

### 3.1 `content.js` — The Brain

The content script is injected into every page. It owns the user-facing behavior.

**Responsibilities:**
- Bootstraps all sub-modules on page load
- Listens for messages from background worker (provider switched, settings changed)
- Manages lifecycle across soft navigations (SPAs like ChatGPT that don't reload the page)

```javascript
// Initialization sequence
async function init() {
  const settings = await loadSettings();
  focusWatcher.start(onInputFocused, onInputBlurred);
  responseWatcher.start(settings.ttsEnabled);
  orchestrator.init(settings);
}

// React to SPA navigation (ChatGPT, Claude etc. use pushState)
const observer = new MutationObserver(debounce(reinit, 500));
observer.observe(document.body, { childList: true, subtree: true });
```

---

### 3.2 `focus-watcher.js` — Input Detection

Watches for the user focusing on any text input across the entire DOM.

**What it targets:**
- `<textarea>` elements
- `<input type="text">` and `<input type="search">`
- `[contenteditable="true"]` divs (used by ChatGPT, Claude, Notion, etc.)
- Shadow DOM inputs (requires special handling)

**How it works:**
```javascript
document.addEventListener('focusin', (e) => {
  const el = e.target;
  if (isTextInput(el)) {
    currentInput = el;
    micButton.attachTo(el);
  }
});

document.addEventListener('focusout', (e) => {
  // Small delay — user may be clicking the mic button itself
  setTimeout(() => {
    if (!micButton.element.contains(document.activeElement)) {
      micButton.detach();
    }
  }, 200);
});

function isTextInput(el) {
  return (
    el.tagName === 'TEXTAREA' ||
    (el.tagName === 'INPUT' && ['text', 'search', 'email', ''].includes(el.type)) ||
    el.isContentEditable
  );
}
```

---

### 3.3 `mic-button.js` — Floating UI

A small, unobtrusive mic button that appears near the focused input.

**Design principles:**
- Never covers the text being typed
- Positions itself in the bottom-right corner of the input, outside the border
- Disappears when input loses focus (with a short grace period)
- Shows recording state (pulsing animation), connecting state, and error state

**States:**
```
idle         → grey mic icon, static
recording    → red, pulsing ring animation
processing   → spinner (audio sent, waiting for transcription)
error        → yellow warning icon (backend unreachable, mic denied, etc.)
```

**Text injection (the tricky part):**

Plain `element.value = text` breaks React/Vue apps because they use synthetic events. The correct approach:

```javascript
function injectText(element, text) {
  if (element.isContentEditable) {
    // For contenteditable divs (ChatGPT, Claude, Notion)
    const selection = window.getSelection();
    const range = selection.getRangeAt(0);
    range.deleteContents();
    range.insertNode(document.createTextNode(text));
  } else {
    // For textarea/input — must fire native events for React
    const nativeInputValueSetter = Object.getOwnPropertyDescriptor(
      window.HTMLTextAreaElement.prototype, 'value'
    ).set;
    nativeInputValueSetter.call(element, text);
    element.dispatchEvent(new Event('input', { bubbles: true }));
    element.dispatchEvent(new Event('change', { bubbles: true }));
  }
}
```

---

### 3.4 `response-watcher.js` — AI Response Detection

Uses a `MutationObserver` to detect when an AI response finishes streaming and triggers TTS playback.

**Three-tier detection strategy:**

**Tier 1 — Site registry (preferred):**
Exact CSS selectors for known apps. Fast, reliable, zero false positives.

**Tier 2 — Heuristic detection (fallback):**
For unknown apps. Watches for new DOM nodes that:
- Contain > 20 words of text
- Appear within 30 seconds after user submits an input
- Are not authored by the user (not inside the input area)
- Are inside a scrollable container (typical of chat UIs)

**Tier 3 — User-defined selector (override):**
User can paste a CSS selector in the popup for any site. Always wins.

**Streaming completion detection:**
AI responses stream in token by token. You don't want TTS to start mid-sentence. Detect completion by:
- Site-specific: watch for the streaming indicator to disappear (`[data-is-streaming]`, stop button, etc.)
- Generic: text stops changing for 1.5 seconds after last mutation

```javascript
let debounceTimer;
const observer = new MutationObserver((mutations) => {
  for (const mutation of mutations) {
    if (isAIResponseNode(mutation.target)) {
      clearTimeout(debounceTimer);
      debounceTimer = setTimeout(() => {
        const text = extractCleanText(mutation.target);
        orchestrator.speak(text);
      }, 1500); // Wait for streaming to settle
    }
  }
});
```

---

### 3.5 `background.js` — Service Worker

The coordinator. Does not touch the DOM.

**Responsibilities:**
- **Backend probe:** Pings `localhost:8000/health` every 30 seconds. Broadcasts provider change to all tabs when status flips.
- **Settings store:** Reads/writes `chrome.storage.sync`. Single source of truth.
- **Offscreen document lifecycle:** Creates/destroys the offscreen document for audio recording.
- **Message routing:** Relays messages between content scripts and providers.

```javascript
// Backend health probe
async function probeBackend() {
  try {
    const res = await fetch('http://localhost:8000/health', { signal: AbortSignal.timeout(2000) });
    const wasOnline = backendOnline;
    backendOnline = res.ok;
    if (wasOnline !== backendOnline) {
      broadcastToAllTabs({ type: 'PROVIDER_CHANGED', backend: backendOnline });
    }
  } catch {
    backendOnline = false;
  }
}

setInterval(probeBackend, 30_000);
probeBackend(); // Run immediately on startup
```

---

### 3.6 `offscreen.js` — Audio Recording

Chrome MV3 content scripts cannot reliably own `getUserMedia`. The Offscreen Document API solves this — a hidden background page that holds the microphone.

**Why offscreen and not the service worker?**
Service workers in MV3 don't have access to Web Audio APIs. The offscreen document is the designated solution for this exact use case.

**Flow:**
```
content.js                background.js              offscreen.js
    │                          │                          │
    │── START_RECORDING ──────►│                          │
    │                          │── CREATE_OFFSCREEN ─────►│
    │                          │                          │── getUserMedia()
    │                          │                          │── MediaRecorder.start()
    │                          │                          │
    │── STOP_RECORDING ───────►│                          │
    │                          │── STOP_RECORDING ───────►│
    │                          │                          │── MediaRecorder.stop()
    │                          │◄── AUDIO_BLOB ───────────│
    │◄── TRANSCRIPTION_RESULT ─│                          │
```

**Ambient VAD integration:**
`ambient-vad.js` from VocalTwist runs inside `offscreen.js`. It processes the audio stream continuously and only fires when voiced speech is detected, filtering silence automatically.

---

## 4. Provider System

### 4.1 STT Providers

**`stt-native.js` — Browser Web Speech API**

```javascript
class NativeSTTProvider {
  start(language, onResult, onError) {
    this.recognition = new webkitSpeechRecognition();
    this.recognition.continuous = false;
    this.recognition.interimResults = true;
    this.recognition.lang = language || 'en-US';
    
    this.recognition.onresult = (e) => {
      const transcript = Array.from(e.results)
        .map(r => r[0].transcript).join('');
      onResult(transcript, e.results[e.results.length - 1].isFinal);
    };
    this.recognition.start();
  }
  stop() { this.recognition?.stop(); }
}
```

> **Note:** Web Speech API in Chrome actually sends audio to Google's servers by default. For truly local speech recognition without a VocalTwist backend, the only real option is WebAssembly-compiled Whisper (whisper.cpp via WASM) — but this is 30-150MB download and CPU-intensive. For v1, accept the Web Speech API trade-off and document it clearly. Users who need true privacy will run VocalTwist.

**`stt-vocaltwist.js` — VocalTwist Whisper Backend**

```javascript
class VocalTwistSTTProvider {
  async transcribe(audioBlob, language) {
    const formData = new FormData();
    formData.append('audio', audioBlob, 'recording.webm');
    if (language) formData.append('language', language);

    const res = await fetch(`${backendUrl}/transcribe`, {
      method: 'POST',
      headers: apiKey ? { 'X-API-Key': apiKey } : {},
      body: formData,
    });
    const data = await res.json();
    return data.text;
  }
}
```

---

### 4.2 TTS Providers

**`tts-native.js` — Browser speechSynthesis**

```javascript
class NativeTTSProvider {
  speak(text, { voice, rate, pitch, onEnd } = {}) {
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.voice = this.selectVoice(voice);
    utterance.rate = rate || 1.0;
    utterance.pitch = pitch || 1.0;
    utterance.onend = onEnd;
    speechSynthesis.speak(utterance);
  }
  stop() { speechSynthesis.cancel(); }
}
```

**`tts-vocaltwist.js` — VocalTwist Edge Neural TTS**

```javascript
class VocalTwistTTSProvider {
  async speak(text, { voice, language, onEnd } = {}) {
    const res = await fetch(`${backendUrl}/speak`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(apiKey ? { 'X-API-Key': apiKey } : {}),
      },
      body: JSON.stringify({ text, voice, language }),
    });
    
    const audioBuffer = await res.arrayBuffer();
    const audioCtx = new AudioContext();
    const decoded = await audioCtx.decodeAudioData(audioBuffer);
    const source = audioCtx.createBufferSource();
    source.buffer = decoded;
    source.connect(audioCtx.destination);
    source.onended = onEnd;
    source.start();
    this.currentSource = source;
  }
  stop() { this.currentSource?.stop(); }
}
```

---

## 5. AI Response Detection Strategy

### Site Registry (`selectors/site-registry.json`)

```json
{
  "chat.openai.com": {
    "responseSelector": "[data-message-author-role='assistant'] .markdown",
    "streamingIndicator": "[data-testid='stop-button']",
    "inputSelector": "#prompt-textarea"
  },
  "claude.ai": {
    "responseSelector": "[data-is-streaming='false'] .font-claude-message",
    "streamingIndicator": "button[aria-label='Stop Response']",
    "inputSelector": "[contenteditable='true'].ProseMirror"
  },
  "gemini.google.com": {
    "responseSelector": "model-response .response-content",
    "streamingIndicator": null,
    "inputSelector": ".ql-editor"
  },
  "perplexity.ai": {
    "responseSelector": ".prose",
    "streamingIndicator": null,
    "inputSelector": "textarea"
  },
  "copilot.microsoft.com": {
    "responseSelector": "[data-content='ai-message']",
    "streamingIndicator": null,
    "inputSelector": "textarea"
  }
}
```

This file is community-maintainable — open a PR to add a new app, no code changes needed.

---

## 6. Phase-by-Phase Build Plan

### Phase 1 — Foundation: STT into Any Input (Days 1–3)

**Goal:** Install extension → focus any textarea → click mic → text appears.

**Deliverables:**
- `manifest.json` with MV3 config
- `background.js` with settings store and backend probe
- `offscreen.html/js` with `getUserMedia` + `MediaRecorder`
- `focus-watcher.js` detecting text inputs
- `mic-button.js` with idle/recording/processing states
- `stt-native.js` (Web Speech API) working end-to-end
- Text injection working in React apps (native event setter pattern)

**Success criteria:** Can dictate into ChatGPT's input box, Gmail compose, and a plain `<textarea>` on a test page.

---

### Phase 2 — TTS Response Playback (Days 4–5)

**Goal:** AI response finishes → spoken aloud automatically.

**Deliverables:**
- `response-watcher.js` with site registry + heuristic fallback
- `tts-native.js` using `speechSynthesis`
- Streaming completion detection (1.5s debounce + streaming indicator watch)
- Mute/replay button injected next to AI response
- Auto-stop TTS when user starts a new recording

**Success criteria:** ChatGPT and Claude responses are read aloud after streaming completes. Mute button works. Starting a new recording cancels current TTS.

---

### Phase 3 — VocalTwist Backend Integration (Days 6–8)

**Goal:** Users with VocalTwist running locally get automatic quality upgrade.

**Deliverables:**
- `stt-vocaltwist.js` — POST to `/transcribe`
- `tts-vocaltwist.js` — POST to `/speak` + AudioContext playback
- Backend probe in `background.js` with 30s interval
- Provider hot-swap without page reload
- Status indicator in popup (🟢 VocalTwist / 🔵 Browser built-in)
- Offscreen document wired to send audio blob to background → VocalTwist provider

**Success criteria:** Start VocalTwist backend → extension auto-upgrades within 30s. Stop backend → extension falls back gracefully. Quality difference is audible.

---

### Phase 4 — Ambient VAD Mode (Days 9–10)

**Goal:** Always-on listening — stop pushing a button, just talk.

**Deliverables:**
- Port `ambient-vad.js` from VocalTwist into `offscreen.js`
- Continuous audio stream with VAD-gated recording
- Visual always-on indicator (small animated dot near focused input)
- Toggle in popup: Push-to-talk vs Ambient mode
- Smart pause: stop ambient listening while TTS is playing (prevents feedback loop)
- Resume ambient listening after TTS completes

**Success criteria:** Enable ambient mode → speak naturally → text appears without button press. No false triggers on background noise. No feedback loop between TTS output and mic.

---

### Phase 5 — Polish, Settings & Store Prep (Days 11–14)

**Goal:** Something you'd be proud to publish.

**Deliverables:**

*Settings popup:*
- Backend URL (default: `http://localhost:8000`)
- API key field (optional, for VocalTwist auth)
- Language selector (en, hi, es, fr, de, zh, etc.)
- Voice selector (populated from available voices)
- STT mode toggle: Push-to-talk / Ambient
- TTS toggle: Auto-read responses on/off
- Per-site disable toggle
- Custom response selector field (power user escape hatch)
- Connection status dot with last-seen timestamp

*Onboarding:*
- First-run welcome overlay explaining the two modes
- "Is your VocalTwist server running?" setup guide with link
- Keyboard shortcut reminder: `Ctrl+Shift+V` to toggle mic

*Polish:*
- Keyboard shortcut `Ctrl+Shift+V` as alternative to clicking mic button
- Smooth animations on mic button (no jarring state changes)
- Error messages that are human-readable ("Can't reach VocalTwist — using browser mode")
- Extension icon badge showing recording state (red dot when active)

*Store prep:*
- Privacy policy (audio processed locally, never stored)
- Screenshots and demo GIF
- Store description emphasizing privacy + local processing angle

---

## 7. Manifest & Permissions

```json
{
  "manifest_version": 3,
  "name": "VocalTwist — Local Voice for Any App",
  "version": "1.0.0",
  "description": "Self-hosted voice I/O for any web app. Speak into any text field. Hear AI responses. Zero cloud. Powered by Whisper + Edge TTS.",

  "permissions": [
    "activeTab",
    "storage",
    "offscreen"
  ],

  "optional_permissions": [
    "microphone"
  ],

  "host_permissions": [
    "<all_urls>"
  ],

  "background": {
    "service_worker": "background.js",
    "type": "module"
  },

  "content_scripts": [
    {
      "matches": ["<all_urls>"],
      "js": ["content/content.js"],
      "css": ["content/content.css"],
      "run_at": "document_idle"
    }
  ],

  "action": {
    "default_popup": "popup/popup.html",
    "default_icon": {
      "16": "icons/icon16.png",
      "32": "icons/icon32.png",
      "48": "icons/icon48.png",
      "128": "icons/icon128.png"
    }
  },

  "commands": {
    "toggle-mic": {
      "suggested_key": {
        "default": "Ctrl+Shift+V",
        "mac": "Command+Shift+V"
      },
      "description": "Toggle microphone recording"
    }
  },

  "icons": {
    "16": "icons/icon16.png",
    "48": "icons/icon48.png",
    "128": "icons/icon128.png"
  }
}
```

**Permission justification for Chrome Web Store review:**
- `activeTab` — inject mic button into the focused tab
- `storage` — save user settings (backend URL, language, preferences)
- `offscreen` — required to own `getUserMedia` in MV3 (standard pattern)
- `<all_urls>` — extension must work on any website the user visits
- `microphone` — requested at runtime on first use, not at install

---

## 8. Key Technical Patterns

### 8.1 Message Protocol

All communication between layers uses typed messages. Define them once in `shared/messages.js`:

```javascript
export const MSG = {
  // Background ↔ Content
  PROVIDER_CHANGED:      'PROVIDER_CHANGED',       // { backend: bool }
  SETTINGS_UPDATED:      'SETTINGS_UPDATED',       // { settings: object }

  // Content → Background
  START_RECORDING:       'START_RECORDING',        // { language: string }
  STOP_RECORDING:        'STOP_RECORDING',         // {}
  SPEAK_TEXT:            'SPEAK_TEXT',             // { text: string }
  STOP_SPEAKING:         'STOP_SPEAKING',          // {}

  // Background ↔ Offscreen
  OFFSCREEN_RECORD_START: 'OFFSCREEN_RECORD_START',
  OFFSCREEN_RECORD_STOP:  'OFFSCREEN_RECORD_STOP',
  OFFSCREEN_AUDIO_READY:  'OFFSCREEN_AUDIO_READY', // { blob: ArrayBuffer }
};
```

### 8.2 SPA Navigation Handling

ChatGPT, Claude, and most AI apps are SPAs. The page never fully reloads, but the DOM changes entirely. Handle this:

```javascript
// In content.js — watch for SPA route changes
let lastUrl = location.href;
const navObserver = new MutationObserver(() => {
  if (location.href !== lastUrl) {
    lastUrl = location.href;
    // Reinitialize watchers after a small delay
    // (let the new page's DOM settle first)
    setTimeout(reinitializeAll, 750);
  }
});
navObserver.observe(document.body, { childList: true, subtree: true });
```

### 8.3 Feedback Loop Prevention

Without this, ambient mode causes TTS audio to be picked up by the mic, creating an infinite loop:

```javascript
class VoiceOrchestrator {
  async speak(text) {
    this.isSpeaking = true;
    if (this.ambientMode) {
      offscreen.pauseVAD(); // Tell offscreen doc to stop listening
    }
    await ttsProvider.speak(text, {
      onEnd: () => {
        this.isSpeaking = false;
        if (this.ambientMode) {
          setTimeout(() => offscreen.resumeVAD(), 500); // Small buffer
        }
      }
    });
  }
}
```

### 8.4 Settings Schema

```javascript
const DEFAULT_SETTINGS = {
  enabled: true,                    // Master on/off
  backendUrl: 'http://localhost:8000',
  apiKey: '',
  language: 'en-US',
  voice: 'auto',                    // 'auto' = pick based on language
  sttMode: 'push-to-talk',          // 'push-to-talk' | 'ambient'
  ttsEnabled: true,                 // Auto-read AI responses
  disabledSites: [],                // ['example.com', ...]
  customSelectors: {},              // { 'myapp.com': '.ai-response' }
  ttsSpeed: 1.0,
  showMicButton: true,
};
```

---

## 9. Popup UI Spec

```
┌─────────────────────────────────────────┐
│  🎙 VocalTwist                    ⚙️    │
│                                         │
│  ● VocalTwist (High Quality)            │  ← green dot when backend up
│    localhost:8000 · Connected           │     blue dot when browser-native
│                                         │
│  ─────────────────────────────────────  │
│                                         │
│  Mode                                   │
│  ○ Push-to-talk   ● Ambient             │
│                                         │
│  Language          Voice                │
│  [ English ▾ ]    [ Auto ▾ ]            │
│                                         │
│  ─────────────────────────────────────  │
│                                         │
│  [✓] Auto-read AI responses             │
│  [✓] Show mic button on text fields     │
│  [ ] Disable on this site               │
│                                         │
│  ─────────────────────────────────────  │
│                                         │
│  Advanced ▾                             │
│    Backend URL: [localhost:8000      ]  │
│    API Key:     [••••••••••          ]  │
│    Custom CSS selector for this site:   │
│    [                                 ]  │
│                                         │
│  Shortcut: Ctrl+Shift+V                 │
└─────────────────────────────────────────┘
```

---

## 10. Testing Strategy

### Unit Tests (Jest)

- `focus-watcher`: detects textarea, input[text], contenteditable; ignores input[password], input[checkbox]
- `text-injector`: correctly fires native React events; handles contenteditable
- `response-watcher`: site registry lookup; heuristic fallback; streaming debounce
- `provider-switcher`: backend probe success → switches to VocalTwist; probe fail → falls back to native

### Integration Tests (Playwright)

Create a local test harness (`test/fixtures/`) with mock pages:
- `plain-textarea.html` — basic textarea
- `react-input.html` — React-controlled input
- `mock-chatbot.html` — simulates streaming AI response

Test scenarios:
1. Focus textarea → mic button appears
2. Click mic → speak → text injected correctly
3. AI response streams in → TTS fires after 1.5s
4. Backend goes offline → provider falls back automatically
5. SPA navigation → watchers reinitialize

### Manual Test Checklist (before each release)

- [ ] ChatGPT — dictate into input, response read aloud
- [ ] Claude.ai — same
- [ ] Gmail — dictate email body
- [ ] Google Docs — dictate into document
- [ ] Notion — dictate into block
- [ ] Slack — dictate into message box
- [ ] Plain HTML form — basic textarea
- [ ] Incognito mode — works correctly
- [ ] Multiple tabs — no cross-tab interference

---

## 11. Chrome Web Store Launch Checklist

### Privacy & Permissions
- [ ] Privacy policy URL hosted and live
- [ ] Microphone permission requested at runtime (not at install)
- [ ] `<all_urls>` justified clearly in store listing
- [ ] No user data sent to third parties (document this explicitly)
- [ ] Web Speech API disclaimer: "Browser built-in mode uses Google's speech servers; run VocalTwist locally for true privacy"

### Store Listing Assets
- [ ] 128×128 icon (clean, recognizable at small sizes)
- [ ] 1280×800 or 640×400 screenshots (at least 3)
- [ ] Short description (132 chars): *"Self-hosted voice I/O for any web app. Speak into inputs, hear AI responses. Uses your local Whisper + Edge TTS — no cloud."*
- [ ] Demo video or GIF showing the core loop
- [ ] Detailed description with setup instructions for VocalTwist backend

### Pre-submission
- [ ] Test on fresh Chrome profile (no existing settings)
- [ ] Test extension update path (increment version, reload, verify settings persist)
- [ ] Confirm no `eval()` or remote code execution (MV3 requirement)
- [ ] Run `web-ext lint` (Mozilla's linter catches common issues)

---

## 12. What You Reuse from VocalTwist

| VocalTwist Component | Reuse in Extension | How |
|---|---|---|
| `backend/` FastAPI router | ✅ **Unchanged** | Extension calls it via fetch — zero backend changes needed |
| `frontend/ambient-vad.js` | ✅ **Copy as-is** | Runs inside `offscreen.js` — exact same file, no changes |
| `frontend/vocal-twist.js` VocalTwistTTS class | ✅ **Extract** | Pull the TTS playback logic into `tts-vocaltwist.js` |
| `frontend/vocal-twist.js` VocalTwistRecorder | ⚠️ **Adapt** | Recording logic moves to offscreen doc; adapt for blob-based flow |
| `VocalTwistTest/` demo chatbot | 📖 **Reference** | Use `chatbot.js` as reference for site-specific integration patterns |
| Docker / docker-compose | ✅ **Unchanged** | Users run VocalTwist backend exactly as documented |
| `openapi.yaml` | 📖 **Reference** | Use as spec when writing `stt-vocaltwist.js` and `tts-vocaltwist.js` |
| `<vocal-twist>` web component | ❌ **Skip** | Extension injects its own lighter UI; web component is overkill here |

---

## Appendix: Open Questions to Decide

1. **WASM Whisper for v2?** Running `whisper.cpp` compiled to WebAssembly would enable true in-browser local STT without any backend. Model download is 30–150MB. Worth considering for v2 as a "no server needed" power mode.

2. **Firefox support?** The Offscreen Document API is Chrome-only. Firefox uses a different pattern (`browser.tabs.create` with `hidden: true`). Decide upfront if Firefox is in scope — it changes the audio recording architecture.

3. **Streaming TTS?** Currently the plan POSTs text → gets full audio back → plays. VocalTwist supports this. A future upgrade: stream audio chunks as the backend generates them, reducing first-audio latency. VocalTwist would need a `/speak/stream` WebSocket endpoint.

4. **Voice interruption?** Full-duplex behaviour (speaking interrupts TTS playback, like VocalTwist demo) is achievable. It means starting VAD while TTS is playing, detecting voice onset, and stopping playback immediately. Nice-to-have for v1.5.
