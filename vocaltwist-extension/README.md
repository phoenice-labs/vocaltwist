# VocalTwist Chrome Extension

> Self-hosted voice I/O for any web app. Speak into any text field. Hear AI responses read back. Zero cloud.

## Features

- 🎙 **Speak into any text field** — works on ChatGPT, Claude, Gmail, Notion, Slack, and any website
- 🔊 **AI response read aloud** — auto-detects when AI responses finish streaming and reads them back
- 🟢 **Whisper quality** — when VocalTwist backend is running locally, upgrades automatically
- 🔵 **Zero setup** — works out of the box with browser's built-in Web Speech API
- 🔒 **Privacy-first** — run VocalTwist locally for 100% local processing, no cloud

## Architecture

```
Extension ─► Browser Native (Web Speech API + speechSynthesis)   [always works]
          └► VocalTwist Backend (Whisper STT + Edge Neural TTS)  [auto-detected]
```

The extension pings `localhost:8000/health` every 30 seconds. If your VocalTwist server is running, it switches providers automatically. Stop the server and it falls back gracefully.

## Installation (Developer Mode)

1. Clone this repo and navigate to `vocaltwist-extension/`
2. Open Chrome → `chrome://extensions`
3. Enable **Developer mode** (top right)
4. Click **Load unpacked** → select the `vocaltwist-extension/` directory
5. The 🎙 VocalTwist icon appears in your toolbar

## Optional: High Quality Mode

Run VocalTwist backend for Whisper STT and Neural TTS:

```bash
# Using Docker (recommended)
docker-compose up

# Or directly
pip install -r requirements.txt
python -m uvicorn backend.main:app --port 8000
```

Once running, the extension automatically upgrades to high-quality mode (green dot in popup).

## Usage

| Action | How |
|--------|-----|
| Start recording | Click the 🎙 button near any text field, or press `Ctrl+Shift+V` |
| Stop recording | Click again or press `Ctrl+Shift+V` |
| Mute TTS | Click the 🔇 button next to an AI response |
| Replay response | Click the 🔊 button |
| Settings | Click the VocalTwist toolbar icon |

## Settings

| Setting | Description |
|---------|-------------|
| Mode | Push-to-talk or Ambient (requires VocalTwist backend) |
| Language | STT language (en-US, hi-IN, es-ES, etc.) |
| Voice | TTS voice selection |
| Auto-read AI responses | Automatically speak AI responses when streaming completes |
| Per-site disable | Disable VocalTwist on specific websites |
| Backend URL | Point to your VocalTwist server (default: `http://localhost:8000`) |
| API Key | Optional authentication for your VocalTwist server |

## Repository Structure

```
vocaltwist-extension/
├── manifest.json              # MV3 config
├── background.js              # Service worker
├── offscreen.html/js          # Hidden audio document
├── content/
│   ├── content.js             # Main injected script
│   ├── content.css            # Floating UI styles
│   ├── focus-watcher.js       # Detects active text inputs
│   ├── mic-button.js          # Floating mic button UI
│   ├── response-watcher.js    # MutationObserver for AI replies
│   └── voice-orchestrator.js  # Coordinates STT/TTS, swaps providers
├── providers/
│   ├── stt-native.js          # webkitSpeechRecognition wrapper
│   ├── stt-vocaltwist.js      # POST to /transcribe
│   ├── tts-native.js          # speechSynthesis wrapper
│   └── tts-vocaltwist.js      # POST to /speak + AudioContext
├── popup/
│   ├── popup.html/js/css      # Settings UI
│   └── onboarding.html        # First-run welcome page
├── selectors/
│   └── site-registry.json     # AI app CSS selectors (community-maintainable)
├── shared/
│   ├── constants.js
│   └── messages.js
├── vendor/
│   └── ambient-vad.js         # Ported from VocalTwist frontend
└── icons/
```

## Privacy

- **Browser mode:** Uses Chrome's Web Speech API, which sends audio to Google's servers
- **VocalTwist mode:** All audio processing happens locally — nothing leaves your machine
- No user data is stored or transmitted beyond what's required for real-time transcription

## Adding a New AI App

Edit `selectors/site-registry.json` — no code changes needed:

```json
"myapp.example.com": {
  "responseSelector": ".ai-response",
  "streamingIndicator": ".stop-button",
  "inputSelector": "textarea"
}
```
