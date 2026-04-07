# VocalTwist — Integration Guide

> **What this guide covers:** How to add voice input (Speech-to-Text), voice output (Text-to-Speech), and ambient listening to any existing web application, chatbot, or agentic AI pipeline.  Each section explains what the code does, what it communicates with, and which parts can be independently enabled or disabled.

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview)
2. [Quick Start — Self-hosted Server](#2-quick-start--self-hosted-server)
3. [Drop-in Frontend Integration](#3-drop-in-frontend-integration)
   - 3a. [Push-to-Talk (PTT)](#3a-push-to-talk-ptt)
   - 3b. [Text-to-Speech Playback](#3b-text-to-speech-playback)
   - 3c. [Ambient Listening (Hands-Free)](#3c-ambient-listening-hands-free)
   - 3d. [All-in-one VocalTwist Object](#3d-all-in-one-vocaltwist-object)
4. [REST API Reference](#4-rest-api-reference)
   - 4a. [POST /api/transcribe](#4a-post-apitranscribe)
   - 4b. [POST /api/transcribe-ambient](#4b-post-apitranscribe-ambient)
   - 4c. [POST /api/speak](#4c-post-apispeak)
   - 4d. [GET /api/health](#4d-get-apihealth)
   - 4e. [GET /api/voices](#4e-get-apivoices)
5. [Existing Chatbot Integration](#5-existing-chatbot-integration)
6. [Agentic AI / Man-in-the-Middle Voice Notes](#6-agentic-ai--man-in-the-middle-voice-notes)
7. [Server-Side Integration (Python / Node / any language)](#7-server-side-integration-python--node--any-language)
8. [Enabling and Disabling Features](#8-enabling-and-disabling-features)
9. [Language and Voice Selection](#9-language-and-voice-selection)
10. [Environment Variables Reference](#10-environment-variables-reference)
11. [Security Considerations](#11-security-considerations)
12. [Troubleshooting](#12-troubleshooting)

---

## 1. Architecture Overview

```
┌─────────────────────────────────────────────────────────────┐
│  Browser / Client                                           │
│                                                             │
│  ┌────────────┐   audio blob   ┌─────────────────────────┐ │
│  │ Microphone │──────────────▶│ VocalTwist Frontend JS  │ │
│  └────────────┘               │  vocal-twist.js          │ │
│                               │  ambient-vad.js          │ │
│  ┌────────────┐  audio bytes  └──────────┬──────────────┘  │
│  │ Speakers   │◀─────────────────────────┘  REST calls      │
│  └────────────┘                             ↕               │
└─────────────────────────────────────────────|───────────────┘
                                              │ HTTP / HTTPS
┌─────────────────────────────────────────────▼───────────────┐
│  VocalTwist Backend  (FastAPI)                              │
│                                                             │
│  POST /api/transcribe  ──▶  faster-whisper (local STT)     │
│  POST /api/speak       ──▶  edge-tts (Microsoft neural TTS)│
│  GET  /api/health                                           │
│  GET  /api/voices                                           │
└─────────────────────────────────────────────────────────────┘
```

**Key design principle:** VocalTwist is a **side-car service**.  Your application never changes its own logic — it only calls two extra REST endpoints: one to convert audio → text before sending to your AI, and one to convert text → audio after receiving your AI's reply.

---

## 2. Quick Start — Self-hosted Server

### Prerequisites
- Python 3.10 or later  
- A virtual environment (`python -m venv .venv`)
- *(Optional)* LM Studio running locally for the demo chatbot

### Start the server

```powershell
# Windows PowerShell
.\start.ps1

# Or manually
.\.venv\Scripts\activate
uvicorn VocalTwistTest.app:app --reload --port 8000
```

The server exposes all endpoints at `http://localhost:8000/api/`.  
Open `http://localhost:8000/` in your browser to see the demo chatbot.

### Stop the server

```powershell
.\start.ps1 -Action stop
```

---

## 3. Drop-in Frontend Integration

Add these two `<script>` tags to any HTML page.  No build step is required.

```html
<!-- Step 1: Load VocalTwist frontend library from your server -->
<script src="http://localhost:8000/vocal-twist.js"></script>

<!-- Step 2 (only needed for Ambient mode): Load ONNX Runtime + VAD-Web -->
<script src="https://cdn.jsdelivr.net/npm/onnxruntime-web/dist/ort.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@ricky0123/vad-web/dist/bundle.min.js"></script>
<script src="http://localhost:8000/ambient-vad.js"></script>
```

---

### 3a. Push-to-Talk (PTT)

**What it does:** Captures microphone audio while the user holds a button, then sends the audio to `/api/transcribe` and returns the transcript as a string.

```html
<button id="ptt-btn">🎤 Hold to Talk</button>
<p id="transcript"></p>

<script>
  const recorder = new VocalTwistRecorder();

  // Optional: live microphone level feedback (0.0 – 1.0)
  recorder.onLevel = (level) => {
    document.getElementById('ptt-btn').style.opacity = 0.4 + level * 0.6;
  };

  // Start recording when user presses the button
  document.getElementById('ptt-btn').addEventListener('mousedown', async () => {
    await recorder.start();
  });

  // Stop and transcribe when user releases
  document.getElementById('ptt-btn').addEventListener('mouseup', async () => {
    const blob = await recorder.stop();  // returns a Blob (audio/webm)

    // Send audio to the VocalTwist backend for transcription
    const formData = new FormData();
    formData.append('audio', blob, 'recording.webm');

    const resp = await fetch('http://localhost:8000/api/transcribe?language=en', {
      method: 'POST',
      body: formData,
    });
    const data = await resp.json();
    document.getElementById('transcript').textContent = data.text;

    // → data.text       : the transcript string
    // → data.language   : detected language code (e.g. "en")
    // → data.duration_s : processing time in seconds
  });
</script>
```

**To disable PTT:** Simply don't create `VocalTwistRecorder` or don't add the button.

---

### 3b. Text-to-Speech Playback

**What it does:** Sends a text string to `/api/speak`, receives MP3 audio, and plays it through the browser's audio output.

```html
<script>
  const tts = new VocalTwistTTS({ baseUrl: 'http://localhost:8000' });

  async function speak(text, language = 'en') {
    // speak() sends text to /api/speak and plays the returned MP3
    await tts.speak(text, { language });
  }

  // Example: after your AI replies
  speak("Hello! How can I help you today?", 'en');

  // Stop playback immediately (e.g. when user starts speaking again)
  tts.stop();
</script>
```

**Voice override:** Pass an explicit edge-tts voice name to bypass automatic selection.

```js
await tts.speak("Namaste!", { voice: 'hi-IN-SwaraNeural' });
```

**To disable TTS:** Don't call `tts.speak()`.  All other functionality is unaffected.

---

### 3c. Ambient Listening (Hands-Free)

**What it does:** Continuously listens to the microphone using client-side Voice Activity Detection (VAD). When speech is detected, it buffers the audio, sends it to `/api/transcribe-ambient` after a silence period, and fires a callback with the transcript. No button press required.

```html
<!-- Requires onnxruntime-web + vad-web scripts loaded first (see section 3) -->
<script>
  const ambient = new AmbientVAD({
    transcribeUrl : 'http://localhost:8000/api/transcribe-ambient',
    language      : 'en',
    silenceMs     : 1500,    // wait this long after speech ends before transcribing
    maxBufferMs   : 30000,   // force transcription after this much buffered speech

    // Called when a complete utterance is transcribed
    onTranscript: (text, displayText) => {
      console.log('User said:', text);
      sendToYourAI(text);  // ← plug in your own AI call here
    },

    // Called when voice state changes: 'idle' | 'listening' | 'buffering' | 'transcribing'
    onStateChange: (state) => console.log('VAD state:', state),

    onError: (err) => console.error('VAD error:', err),
  });

  await ambient.start();   // request microphone permission + start listening

  // To stop:
  ambient.stop();
</script>
```

**How the VAD works:** `vad-web` runs the Silero VAD ONNX model **entirely in the browser** — no audio is sent to the server until speech is detected and silence ends. This minimises data transmission and latency.

**To disable ambient mode:** Don't load the `ambient-vad.js` script and don't load onnxruntime-web/vad-web. No server-side changes needed.

---

### 3d. All-in-one VocalTwist Object

The `VocalTwist` orchestrator combines PTT + TTS + Ambient into one object with a unified state machine. Use this when you want all three modes managed together (e.g. to prevent TTS playing while the user is speaking).

```js
const vt = new VocalTwist({
  baseUrl  : 'http://localhost:8000',
  language : 'en',
  voice    : 'en-US-AriaNeural',

  onTranscript  : (text) => sendToAI(text),
  onStateChange : (state) => updateUI(state),
  onError       : (err)  => showError(err),
});

await vt.init();

// PTT
await vt.startRecording();
const blob = await vt.stopRecording();

// TTS
await vt.speak("Reply text here");

// Ambient
await vt.startAmbient();
vt.stopAmbient();

// Switch language (updates both STT hints and TTS voice)
vt.setLanguage('hi');
```

---

## 4. REST API Reference

All endpoints are under the `/api/` prefix.  
Authentication is optional (see [Environment Variables](#10-environment-variables-reference)).

---

### 4a. POST /api/transcribe

Converts an audio file to text.

**Request:** `multipart/form-data`

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `audio` | File | ✅ | Audio file (webm, mp3, wav, m4a, ogg, flac) |

**Query parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `language` | string | auto | ISO 639-1 code hint (`en`, `hi`, `mr`, …). Omit for auto-detection. |
| `task` | string | `transcribe` | `transcribe` = return in source language; `translate` = force English output |
| `vad_filter` | bool | `true` | Apply Silero VAD to strip silence before transcription |

**Response:** `application/json`

```json
{
  "text": "Hello, how are you?",
  "language": "en",
  "duration_s": 0.42,
  "request_id": "abc123"
}
```

**cURL example:**
```bash
curl -X POST http://localhost:8000/api/transcribe?language=en \
  -F audio=@recording.webm
```

---

### 4b. POST /api/transcribe-ambient

Identical to `/api/transcribe` but intended for VAD-buffered chunks. Accepts the same parameters. Use this endpoint for ambient listening to keep server-side logs and metrics separated.

---

### 4c. POST /api/speak

Synthesises text to MP3 audio using Microsoft Edge Neural TTS.

**Request:** `application/json`

```json
{
  "text": "Hello! How can I help you?",
  "language": "en",
  "voice": "en-US-AriaNeural"
}
```

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `text` | string | ✅ | Text to synthesise (max 4096 chars; HTML stripped automatically) |
| `language` | string | `en` | ISO 639-1 code; used to auto-select the default voice if `voice` is omitted |
| `voice` | string | *(auto)* | Edge TTS voice name override (e.g. `hi-IN-SwaraNeural`) |

**Response:** `audio/mpeg` (MP3 bytes)  
The raw MP3 bytes are returned — play them directly or write to a file.

**JavaScript example:**
```js
const resp = await fetch('http://localhost:8000/api/speak', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ text: 'Hello!', language: 'en' }),
});
const mp3Bytes = await resp.arrayBuffer();
const audio = new Audio(URL.createObjectURL(new Blob([mp3Bytes], { type: 'audio/mpeg' })));
audio.play();
```

---

### 4d. GET /api/health

Returns service health status. Use this to check if the server is running before attempting other calls.

```json
{
  "status": "ok",
  "stt_provider": "whisper",
  "tts_provider": "edge_tts",
  "version": "0.1.0"
}
```

---

### 4e. GET /api/voices

Returns all available TTS voices grouped by language code.

```json
{
  "voices": [
    { "name": "en-US-AriaNeural",   "language": "en", "gender": "Female" },
    { "name": "hi-IN-SwaraNeural",  "language": "hi", "gender": "Female" },
    ...
  ]
}
```

---

## 5. Existing Chatbot Integration

This section walks through adding voice to an existing chatbot that already has a text-based `/api/chat` endpoint.

### Minimal integration — 3 steps

**Step 1 — Load the frontend library in your HTML `<head>`:**

```html
<script src="http://localhost:8000/vocal-twist.js"></script>
```

**Step 2 — Add a mic button next to your text input:**

```html
<!-- Add this button anywhere near your chat input -->
<button id="mic-btn" type="button" title="Hold to speak">🎤</button>
```

**Step 3 — Wire it up in JavaScript:**

```js
// ── Initialise VocalTwist alongside your existing chat code ──────────────────

const recorder = new VocalTwistRecorder();
const tts      = new VocalTwistTTS({ baseUrl: 'http://localhost:8000' });

const micBtn   = document.getElementById('mic-btn');
const chatInput = document.getElementById('your-text-input');  // ← your existing input

// Push-to-talk: hold button → speak → release → transcript fills chat input
micBtn.addEventListener('mousedown', () => recorder.start());

micBtn.addEventListener('mouseup', async () => {
  const blob = await recorder.stop();

  // Transcribe via VocalTwist
  const fd = new FormData();
  fd.append('audio', blob, 'audio.webm');
  const sttResp = await fetch('http://localhost:8000/api/transcribe?language=en', {
    method: 'POST', body: fd
  });
  const { text } = await sttResp.json();

  // Populate the existing text input with the transcript
  chatInput.value = text;

  // Optionally auto-submit
  chatInput.form?.requestSubmit();
});

// ── After your AI returns a reply, speak it aloud ────────────────────────────
// Call this from wherever you currently display the AI's reply:

async function onAIReply(replyText) {
  displayMessageInUI(replyText);       // ← your existing UI update
  await tts.speak(replyText, { language: 'en' }); // ← NEW: also speak it
}
```

That's the entire integration.  The rest of your chatbot code is untouched.

---

## 6. Agentic AI / Man-in-the-Middle Voice Notes

Use VocalTwist as a **voice I/O shim** inside an AI agent pipeline.  The agent can receive spoken instructions and respond with synthesised speech, without any human-facing UI.

### Pattern A — Agent receives a voice note

The user records a voice note (via PTT or ambient capture). Before passing to the LLM, the audio is converted to text by VocalTwist:

```
Voice Note (audio file)
        │
        ▼ POST /api/transcribe
        │
   Transcript (text)
        │
        ▼  Insert into agent context / tool call
        │
   LLM / Agent
```

**Python agent code:**
```python
import httpx

async def transcribe_voice_note(audio_bytes: bytes, language: str = "en") -> str:
    """
    Send a voice note to VocalTwist and get back the transcript.
    Call this BEFORE passing user input to your LLM.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://localhost:8000/api/transcribe",
            params={"language": language},
            files={"audio": ("audio.webm", audio_bytes, "audio/webm")},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["text"]
```

### Pattern B — Agent sends a spoken reply

The LLM produces a text reply. Before displaying it, VocalTwist converts it to speech:

```
LLM reply (text)
        │
        ▼ POST /api/speak
        │
   MP3 audio bytes
        │
        ▼  Stream to user / save as file / play via audio API
```

**Python agent code:**
```python
async def speak_reply(text: str, language: str = "en") -> bytes:
    """
    Convert LLM reply text to MP3 audio via VocalTwist.
    Returns raw MP3 bytes — play, stream, or save as needed.
    """
    async with httpx.AsyncClient() as client:
        resp = await client.post(
            "http://localhost:8000/api/speak",
            json={"text": text, "language": language},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.content  # raw MP3 bytes
```

### Pattern C — Full round-trip in an agent tool

Wire both calls as a single agent "voice turn" tool:

```python
async def voice_turn(audio_bytes: bytes, language: str = "en") -> dict:
    """
    Full voice round-trip:
      1. Transcribe incoming audio to text
      2. Send text to LLM
      3. Convert LLM reply to speech
    Returns { transcript, reply, audio_bytes }
    """
    # Step 1: STT
    transcript = await transcribe_voice_note(audio_bytes, language)

    # Step 2: LLM (your existing agent call)
    reply = await call_your_llm(transcript, language=language)

    # Step 3: TTS
    audio = await speak_reply(reply, language)

    return {"transcript": transcript, "reply": reply, "audio": audio}
```

### Using with LangChain / LlamaIndex tools

Wrap either call as a `Tool` that the agent can invoke:

```python
from langchain.tools import tool

@tool
def transcribe_audio(audio_base64: str) -> str:
    """Convert base64-encoded audio to text using VocalTwist STT."""
    import asyncio, base64
    audio_bytes = base64.b64decode(audio_base64)
    return asyncio.run(transcribe_voice_note(audio_bytes))

@tool
def synthesise_speech(text: str) -> str:
    """Convert text to speech using VocalTwist TTS. Returns base64 MP3."""
    import asyncio, base64
    mp3 = asyncio.run(speak_reply(text))
    return base64.b64encode(mp3).decode()
```

---

## 7. Server-Side Integration (Python / Node / any language)

VocalTwist is a standard HTTP service — any language can call it.

### Python (`httpx`)

```python
import httpx

async def stt(audio_path: str, language: str = "en") -> str:
    async with httpx.AsyncClient() as c:
        with open(audio_path, "rb") as f:
            r = await c.post(
                "http://localhost:8000/api/transcribe",
                params={"language": language},
                files={"audio": ("file.webm", f, "audio/webm")},
            )
        return r.json()["text"]

async def tts(text: str, language: str = "en") -> bytes:
    async with httpx.AsyncClient() as c:
        r = await c.post(
            "http://localhost:8000/api/speak",
            json={"text": text, "language": language},
        )
        return r.content  # MP3 bytes
```

### Node.js (`node-fetch` / `axios`)

```js
const FormData = require('form-data');
const fs       = require('fs');
const fetch    = require('node-fetch');

// STT
async function transcribe(audioPath, language = 'en') {
  const form = new FormData();
  form.append('audio', fs.createReadStream(audioPath), 'recording.webm');
  const resp = await fetch(
    `http://localhost:8000/api/transcribe?language=${language}`,
    { method: 'POST', body: form }
  );
  const data = await resp.json();
  return data.text;
}

// TTS
async function speak(text, language = 'en') {
  const resp = await fetch('http://localhost:8000/api/speak', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ text, language }),
  });
  return Buffer.from(await resp.arrayBuffer()); // MP3 buffer
}
```

### cURL (shell scripts / CI pipelines)

```bash
# Transcribe
curl -s -X POST "http://localhost:8000/api/transcribe?language=en" \
  -F audio=@recording.webm | jq -r .text

# Speak (save MP3)
curl -s -X POST "http://localhost:8000/api/speak" \
  -H "Content-Type: application/json" \
  -d '{"text":"Hello world","language":"en"}' \
  --output reply.mp3
```

---

## 8. Enabling and Disabling Features

| Feature | How to enable | How to disable |
|---------|--------------|----------------|
| **Push-to-Talk** | Create `VocalTwistRecorder`, add mouse events | Don't create `VocalTwistRecorder` |
| **TTS playback** | Call `VocalTwistTTS.speak()` | Don't call `.speak()` (or call `tts.stop()` to cancel) |
| **Ambient mode** | Load `ambient-vad.js`, `ort.min.js`, `bundle.min.js`; call `AmbientVAD.start()` | Don't load these scripts; or call `ambient.stop()` |
| **API key auth** | Set `VOCALTWIST_API_KEYS=key1,key2` in `.env` | Leave `VOCALTWIST_API_KEYS` empty |
| **Rate limiting** | Set `VOCALTWIST_RATE_LIMIT_STT=20/minute` | Set to empty string or `0/minute` |
| **VAD filter** | Pass `?vad_filter=true` (default) | Pass `?vad_filter=false` |
| **Translation** | Pass `?task=translate` to `/api/transcribe` | Pass `?task=transcribe` (default) |
| **CORS** | Set `VOCALTWIST_ALLOWED_ORIGINS=https://myapp.com` | Set to `*` for open or restrict as needed |

---

## 9. Language and Voice Selection

### Supported languages

| Code | Language | Default Voice |
|------|----------|--------------|
| `en` | English | en-US-AriaNeural |
| `hi` | Hindi | hi-IN-SwaraNeural |
| `mr` | Marathi | mr-IN-AarohiNeural |
| `es` | Spanish | es-ES-ElviraNeural |
| `fr` | French | fr-FR-DeniseNeural |
| `pt` | Portuguese | pt-BR-FranciscaNeural |
| `de` | German | de-DE-KatjaNeural |
| `zh` | Chinese | zh-CN-XiaoxiaoNeural |
| `ja` | Japanese | ja-JP-NanamiNeural |
| `ar` | Arabic | ar-SA-ZariyahNeural |

Run `edge-tts --list-voices` to see all ~400+ available voices.

### Voice resolution order (TTS)

1. Explicit `voice` field in the `/api/speak` request body
2. `voice_map` in `.env` for the given language code
3. Built-in default for the language code (table above)
4. Provider default (`en-US-AriaNeural`)

### Overriding voice map in `.env`

```env
# Map each language code to a preferred voice
VOCALTWIST_VOICE_MAP=en:en-GB-SoniaNeural,hi:hi-IN-MadhurNeural
```

---

## 10. Environment Variables Reference

Copy `.env.example` to `.env` and edit as needed.

| Variable | Default | Description |
|----------|---------|-------------|
| `VOCALTWIST_API_KEYS` | *(empty = no auth)* | Comma-separated API keys. If set, all API requests require `Authorization: Bearer <key>` |
| `VOCALTWIST_ALLOWED_ORIGINS` | `*` | CORS allowed origins (comma-separated) |
| `VOCALTWIST_STT_PROVIDER` | `whisper` | STT backend: `whisper` |
| `VOCALTWIST_TTS_PROVIDER` | `edge_tts` | TTS backend: `edge_tts` |
| `VOCALTWIST_WHISPER_MODEL` | `base` | Whisper model size: `tiny`, `base`, `small`, `medium`, `large-v3` |
| `VOCALTWIST_WHISPER_DEVICE` | `cpu` | `cpu` or `cuda` (requires GPU + CUDA) |
| `VOCALTWIST_DEFAULT_LANGUAGE` | `en` | Fallback language when none is specified |
| `VOCALTWIST_VOICE_MAP` | *(built-in)* | Override language → voice mapping |
| `VOCALTWIST_MAX_AUDIO_MB` | `25` | Maximum audio upload size in megabytes |
| `VOCALTWIST_RATE_LIMIT_STT` | `20/minute` | Rate limit for STT endpoints |
| `VOCALTWIST_RATE_LIMIT_TTS` | `20/minute` | Rate limit for TTS endpoint |

---

## 11. Security Considerations

### Authentication
When deploying on a network-accessible server, always set `VOCALTWIST_API_KEYS`:

```env
VOCALTWIST_API_KEYS=my-secret-key-1,my-secret-key-2
```

All clients must then include the header:

```
Authorization: Bearer my-secret-key-1
```

In the frontend:
```js
const vt = new VocalTwist({
  baseUrl : 'http://localhost:8000',
  headers : { 'Authorization': 'Bearer my-secret-key-1' },
});
```

### CORS
Restrict allowed origins to your own domain in production:

```env
VOCALTWIST_ALLOWED_ORIGINS=https://yourdomain.com
```

### Audio size limits
Default max upload is 25 MB. Reduce this in constrained environments:

```env
VOCALTWIST_MAX_AUDIO_MB=5
```

### Input sanitisation
All text sent to `/api/speak` is automatically HTML-stripped server-side before synthesis, preventing injection of markup into TTS output.

---

## 12. Troubleshooting

| Symptom | Likely cause | Fix |
|---------|-------------|-----|
| `404 /api/transcribe` | Server not running | Run `.\start.ps1` or check server logs |
| STT returns wrong language | Passing no `language` param (auto-detect) | Pass explicit `?language=en` |
| TTS response in wrong language | LLM not instructed to reply in target language | Include language in your system prompt explicitly |
| TTS 403 from Microsoft | Outdated `edge-tts` library | `pip install --upgrade edge-tts` |
| Browser ONNX warnings | Benign ONNX Runtime graph-optimisation messages | Not an error; suppressed in ambient-vad.js |
| Microphone permission denied | Browser blocked mic access | Serve over HTTPS or `localhost`; grant permission in browser settings |
| `ModuleNotFoundError: onnxruntime` | Missing server-side dependency | `pip install onnxruntime` |
| Audio not playing on iOS | iOS requires user gesture to play audio | Trigger `tts.speak()` from inside a click handler |
