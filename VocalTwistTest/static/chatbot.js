/**
 * VocalTwistTest — Demo Chatbot
 *
 * Uses VocalTwist voice middleware to provide:
 *  - Push-to-talk (hold mic button)
 *  - Ambient always-on listening toggle
 *  - Auto-TTS for every assistant reply
 *  - Full-duplex: speech start interrupts current TTS
 *  - Emergency keyword detection with visible alert
 *  - Multi-language support with automatic voice switching
 *  - LM Studio health polling
 *
 * Depends on (loaded before this file):
 *   - onnxruntime-web  (CDN)
 *   - vad-web          (CDN)
 *   - /ambient-vad.js  (served by app.py from frontend/)
 *   - /vocal-twist.js  (served by app.py from frontend/)
 */

'use strict';

// ── Voice map (language → edge-tts neural voice) ──────────────────────────────
const VOICE_MAP = {
  en: 'en-US-AriaNeural',
  hi: 'hi-IN-SwaraNeural',
  es: 'es-ES-ElviraNeural',
  fr: 'fr-FR-DeniseNeural',
  de: 'de-DE-KatjaNeural',
  zh: 'zh-CN-XiaoxiaoNeural',
};

// Maximum messages kept in context for the LLM (to avoid huge payloads)
const MAX_CONTEXT_MESSAGES = 20;

// ── VocalTwistChatbot ─────────────────────────────────────────────────────────

class VocalTwistChatbot {
  /**
   * @param {object} config
   * @param {string} [config.transcribeUrl]
   * @param {string} [config.speakUrl]
   * @param {string} [config.ambientUrl]
   * @param {string} [config.chatUrl]
   * @param {string} [config.healthUrl]
   * @param {string} [config.language]
   */
  constructor(config = {}) {
    this._transcribeUrl = config.transcribeUrl ?? '/api/transcribe';
    this._speakUrl      = config.speakUrl      ?? '/api/speak';
    this._ambientUrl    = config.ambientUrl    ?? '/api/transcribe-ambient';
    this._chatUrl       = config.chatUrl       ?? '/api/chat';
    this._healthUrl     = config.healthUrl     ?? '/api/health';

    this._language      = config.language ?? 'en';
    this._history       = []; // {role, content}[]
    this._thinking      = null; // pending thinking bubble element
    this._ambientActive = false;
    this._micHeld       = false;
    this._llmOnline     = false;
    this._healthTimer   = null;

    /** @type {VocalTwist|null} */
    this._vt = null;

    // DOM references (set in init())
    this._dom = {};
  }

  // ── Initialise ───────────────────────────────────────────────────────────────

  async init() {
    // Cache DOM nodes
    this._dom = {
      messages:       document.getElementById('messages'),
      emptyState:     document.getElementById('empty-state'),
      statusBar:      document.getElementById('status-bar'),
      statusIcon:     document.getElementById('status-icon'),
      statusText:     document.getElementById('status-text'),
      llmStatus:      document.getElementById('llm-status'),
      llmStatusText:  document.getElementById('llm-status-text'),
      txtInput:       document.getElementById('txt-input'),
      btnSend:        document.getElementById('btn-send'),
      btnMic:         document.getElementById('btn-mic'),
      btnAmbient:     document.getElementById('btn-ambient'),
      btnClear:       document.getElementById('btn-clear'),
      levelMeterBar:  document.getElementById('level-meter-bar'),
      emergencyBanner:document.getElementById('emergency-banner'),
      langSelect:     document.getElementById('lang-select'),
    };

    // Initialise VocalTwist
    this._vt = new VocalTwist({
      transcribeUrl : this._transcribeUrl,
      speakUrl      : this._speakUrl,
      ambientUrl    : this._ambientUrl,
      language      : this._language,
      voice         : VOICE_MAP[this._language] ?? null,

      onTranscript  : (text) => this._onTranscript(text),
      onStateChange : (state) => this._onStateChange(state),
      onSpeechStart : () => this._onSpeechStart(),
      onTTSStart    : () => this._setStatus('speaking', '🔊', 'Assistant is speaking…'),
      onTTSEnd      : () => this._setStatus('idle', '💬', 'Ready'),
      onError       : (err) => this._onError(err),
    });

    // Level meter
    this._vt.onLevel = (level) => {
      this._dom.levelMeterBar.style.width = `${Math.round(level * 100)}%`;
    };

    // Wire up controls
    this._bindControls();

    // Start LLM health polling
    this._startHealthPolling();
  }

  // ── Event handlers ───────────────────────────────────────────────────────────

  /** Called when a transcript arrives (push-to-talk or ambient). */
  _onTranscript(text) {
    const trimmed = text.trim();
    if (!trimmed) return;
    // Auto-send transcribed speech as a user message
    this.sendMessage(trimmed);
  }

  /** Called when VocalTwist changes state. */
  _onStateChange(state) {
    const labels = {
      'idle':                   ['💬', 'Ready',                         'idle'],
      'recording':              ['🔴', 'Recording…',                   'recording'],
      'transcribing':           ['⏳', 'Transcribing…',                 'transcribing'],
      'speaking':               ['🔊', 'Assistant is speaking…',        'speaking'],
      'ambient-listening':      ['🟢', 'Listening (ambient)…',          'ambient'],
      'ambient-buffering':      ['🟡', 'Speech detected — buffering…',  'ambient'],
      'ambient-transcribing':   ['⏳', 'Transcribing ambient audio…',   'transcribing'],
    };
    const [icon, text, cls] = labels[state] ?? ['💬', state, 'idle'];
    this._setStatus(cls, icon, text);

    // Keep mic button in sync
    if (state === 'recording') {
      this._dom.btnMic.classList.add('recording');
      this._dom.btnMic.title = 'Release to transcribe';
    } else {
      this._dom.btnMic.classList.remove('recording');
      this._dom.btnMic.title = 'Hold to record (push-to-talk)';
    }
  }

  /**
   * Full-duplex: user starts speaking → immediately stop TTS so there
   * is no overlap between the assistant's voice and the user's words.
   */
  _onSpeechStart() {
    if (this._vt && this._vt.state === 'speaking') {
      this._vt.stopTTS?.();  // graceful stop if available
    }
  }

  _onError(err) {
    console.error('[VocalTwistChatbot]', err);
    this._setStatus('idle', '⚠️', `Error: ${err.message}`);
  }

  // ── Core: send a message ──────────────────────────────────────────────────────

  async sendMessage(text) {
    const trimmed = text.trim();
    if (!trimmed) return;

    // Clear input if the message came from the text box
    this._dom.txtInput.value = '';
    this._dom.btnSend.disabled = true;

    // Hide empty state
    if (this._dom.emptyState) {
      this._dom.emptyState.remove();
      this._dom.emptyState = null;
    }
    // Hide emergency banner from previous turn
    this._dom.emergencyBanner.classList.remove('visible');

    // Push user message to UI + history
    this.addMessage('user', trimmed);
    this._history.push({ role: 'user', content: trimmed });

    // Trim history to keep context manageable
    if (this._history.length > MAX_CONTEXT_MESSAGES) {
      this._history = this._history.slice(-MAX_CONTEXT_MESSAGES);
    }

    this.showThinking();
    this._setStatus('idle', '🤔', 'Thinking…');

    try {
      const resp = await fetch(this._chatUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          messages: this._history,
          language: this._language,
        }),
      });

      if (!resp.ok) {
        const errData = await resp.json().catch(() => ({}));
        throw new Error(errData.detail ?? `HTTP ${resp.status}`);
      }

      const data = await resp.json();
      const reply = data.reply ?? '(no response)';
      const isEmergency = !!data.is_emergency;

      this.hideThinking();

      // Render assistant reply
      this.addMessage('assistant', reply, isEmergency);
      this._history.push({ role: 'assistant', content: reply });

      if (isEmergency) {
        this._dom.emergencyBanner.classList.add('visible');
      }

      // Auto-TTS: speak the assistant's reply
      if (this._vt) {
        try {
          await this._vt.speak(reply);
        } catch (ttsErr) {
          // TTS failure is non-fatal — log and continue
          console.warn('[VocalTwistChatbot] TTS failed:', ttsErr.message);
        }
      }
    } catch (err) {
      this.hideThinking();
      this.addMessage('assistant', `⚠️ ${err.message}`);
      this._setStatus('idle', '⚠️', err.message);
    } finally {
      this._dom.btnSend.disabled = false;
    }
  }

  // ── UI helpers ────────────────────────────────────────────────────────────────

  /**
   * Render a message bubble and scroll into view.
   * @param {'user'|'assistant'} role
   * @param {string} content
   * @param {boolean} [isEmergency]
   */
  addMessage(role, content, isEmergency = false) {
    const wrapper = document.createElement('div');
    wrapper.className = `msg ${role}${isEmergency ? ' emergency' : ''}`;

    const avatar = document.createElement('div');
    avatar.className = 'msg-avatar';
    avatar.textContent = role === 'user' ? '🙂' : '🤖';

    const bubble = document.createElement('div');
    bubble.className = 'msg-bubble';
    // Basic sanitisation — treat content as plain text to prevent XSS
    bubble.textContent = content;

    wrapper.appendChild(avatar);
    wrapper.appendChild(bubble);
    this._dom.messages.appendChild(wrapper);
    wrapper.scrollIntoView({ behavior: 'smooth', block: 'end' });
  }

  showThinking() {
    if (this._thinking) return;
    const wrapper = document.createElement('div');
    wrapper.className = 'msg assistant';

    const avatar = document.createElement('div');
    avatar.className = 'msg-avatar';
    avatar.textContent = '🤖';

    const bubble = document.createElement('div');
    bubble.className = 'thinking';
    bubble.innerHTML = '<div class="thinking-dots"><span></span><span></span><span></span></div>';

    wrapper.appendChild(avatar);
    wrapper.appendChild(bubble);
    this._dom.messages.appendChild(wrapper);
    wrapper.scrollIntoView({ behavior: 'smooth', block: 'end' });
    this._thinking = wrapper;
  }

  hideThinking() {
    if (this._thinking) {
      this._thinking.remove();
      this._thinking = null;
    }
  }

  /**
   * Switch the conversation language and update the TTS voice.
   * @param {string} lang  ISO 639-1 code
   */
  setLanguage(lang) {
    this._language = lang;
    if (this._vt) {
      this._vt.setLanguage(lang);
    }
    // Update voice in VocalTwist config (re-init not required; speak() picks it up)
    this._currentVoice = VOICE_MAP[lang] ?? null;
  }

  // ── Status bar ────────────────────────────────────────────────────────────────

  _setStatus(cls, icon, text) {
    this._dom.statusBar.className = `status-bar ${cls}`;
    this._dom.statusIcon.textContent = icon;
    this._dom.statusText.textContent = text;
  }

  // ── Control bindings ──────────────────────────────────────────────────────────

  _bindControls() {
    const { txtInput, btnSend, btnMic, btnAmbient, btnClear, langSelect } = this._dom;

    // Text send
    btnSend.addEventListener('click', () => this.sendMessage(txtInput.value));
    txtInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        this.sendMessage(txtInput.value);
      }
    });

    // Push-to-talk — pointerdown / pointerup for mouse + touch
    btnMic.addEventListener('pointerdown', async (e) => {
      e.preventDefault();
      if (this._micHeld) return;
      this._micHeld = true;
      try {
        await this._vt.startRecording();
      } catch (err) {
        console.error('[VocalTwistChatbot] startRecording:', err);
        this._micHeld = false;
      }
    });

    const stopMic = async () => {
      if (!this._micHeld) return;
      this._micHeld = false;
      try {
        await this._vt.stopRecording();
      } catch (err) {
        console.error('[VocalTwistChatbot] stopRecording:', err);
      }
    };
    btnMic.addEventListener('pointerup',    stopMic);
    btnMic.addEventListener('pointerleave', stopMic);

    // Ambient toggle
    btnAmbient.addEventListener('click', async () => {
      if (!this._ambientActive) {
        try {
          await this._vt.startAmbient();
          this._ambientActive = true;
          btnAmbient.classList.add('active');
          btnAmbient.textContent = '⏹ Stop Ambient';
          this._setStatus('ambient', '🟢', 'Ambient listening active — speak naturally');
        } catch (err) {
          console.error('[VocalTwistChatbot] startAmbient:', err);
          this._setStatus('idle', '⚠️', `Ambient error: ${err.message}`);
        }
      } else {
        this._vt.stopAmbient();
        this._ambientActive = false;
        btnAmbient.classList.remove('active');
        btnAmbient.textContent = '🔁 Ambient';
        this._setStatus('idle', '💬', 'Ready');
      }
    });

    // Clear conversation
    btnClear.addEventListener('click', () => {
      this._history = [];
      this._dom.messages.innerHTML = '';
      this._dom.emergencyBanner.classList.remove('visible');
      this._setStatus('idle', '💬', 'Conversation cleared');
    });

    // Language selector
    langSelect.addEventListener('change', () => {
      this.setLanguage(langSelect.value);
    });
  }

  // ── LLM health polling ────────────────────────────────────────────────────────

  async checkLLMStatus() {
    try {
      const resp = await fetch(this._healthUrl, { signal: AbortSignal.timeout(4000) });
      const data = await resp.json().catch(() => ({}));
      const isOk = resp.ok && (data.status === 'ok' || data.status === 'healthy');
      this._setLLMOnline(isOk);
    } catch {
      this._setLLMOnline(false);
    }
  }

  _setLLMOnline(online) {
    this._llmOnline = online;
    const el = this._dom.llmStatus;
    const txt = this._dom.llmStatusText;
    if (online) {
      el.className = 'llm-status online';
      txt.textContent = 'LM Studio';
    } else {
      el.className = 'llm-status offline';
      txt.textContent = 'LM Studio offline';
    }
  }

  _startHealthPolling() {
    this.checkLLMStatus();
    this._healthTimer = setInterval(() => this.checkLLMStatus(), 10_000);
  }

  destroy() {
    if (this._healthTimer) clearInterval(this._healthTimer);
    this._vt?.destroy();
  }
}

// ── Bootstrap ─────────────────────────────────────────────────────────────────

document.addEventListener('DOMContentLoaded', async () => {
  const chatbot = new VocalTwistChatbot({
    transcribeUrl : '/api/transcribe',
    speakUrl      : '/api/speak',
    ambientUrl    : '/api/transcribe-ambient',
    chatUrl       : '/api/chat',
    healthUrl     : '/api/health',
    language      : 'en',
  });

  try {
    await chatbot.init();
  } catch (err) {
    console.error('[VocalTwistChatbot] init failed:', err);
    document.getElementById('status-text').textContent =
      `Init error: ${err.message}`;
  }

  // Expose for debugging in DevTools
  window.__chatbot = chatbot;
});
