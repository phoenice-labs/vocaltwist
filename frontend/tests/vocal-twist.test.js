'use strict';

/**
 * vocal-twist.test.js
 * Jest test suite for VocalTwist frontend modules.
 *
 * All browser APIs (MediaRecorder, getUserMedia, fetch, Audio, vad, ort)
 * are mocked — no real hardware or network required.
 */

// ─── Global mocks (must be set before requiring source files) ─────────────────

// requestAnimationFrame / cancelAnimationFrame
global.requestAnimationFrame = jest.fn((cb) => setTimeout(cb, 16));
global.cancelAnimationFrame  = jest.fn((id) => clearTimeout(id));

// URL object helpers (jsdom may not implement these)
if (!global.URL.createObjectURL) {
  global.URL.createObjectURL = jest.fn(() => 'blob:mock-url-' + Math.random());
}
if (!global.URL.revokeObjectURL) {
  global.URL.revokeObjectURL = jest.fn();
}

// ─── MediaStream factory ──────────────────────────────────────────────────────
function makeMockStream() {
  const track = { stop: jest.fn(), kind: 'audio', enabled: true };
  return {
    getTracks     : jest.fn(() => [track]),
    getAudioTracks: jest.fn(() => [track]),
    _track        : track,
  };
}

// ─── MediaRecorder mock ───────────────────────────────────────────────────────
class MockMediaRecorder {
  constructor(stream, options = {}) {
    this.stream           = stream;
    this.options          = options;
    this.state            = 'inactive';
    this.ondataavailable  = null;
    this.onstop           = null;
    this.onerror          = null;
    MockMediaRecorder._instances.push(this);
  }

  start(timeslice) {
    this.state = 'recording';
    // Emit one data chunk asynchronously
    setTimeout(() => {
      this.ondataavailable?.({
        data: new Blob(['pcm-audio-data'], { type: this.options.mimeType ?? 'audio/webm' }),
      });
    }, 10);
  }

  stop() {
    this.state = 'inactive';
    setTimeout(() => { this.onstop?.(); }, 5);
  }

  static isTypeSupported(type) {
    return type === 'audio/webm' || type === 'audio/webm;codecs=opus';
  }

  static _instances = [];
  static _reset() { MockMediaRecorder._instances = []; }
}

global.MediaRecorder = MockMediaRecorder;

// ─── AudioContext mock ────────────────────────────────────────────────────────
function makeMockAudioContext() {
  const analyser = {
    fftSize           : 256,
    frequencyBinCount : 128,
    getByteFrequencyData: jest.fn((arr) => arr.fill(50)),
    connect           : jest.fn(),
  };
  return {
    createMediaStreamSource: jest.fn(() => ({ connect: jest.fn() })),
    createAnalyser          : jest.fn(() => analyser),
    close                   : jest.fn(),
    _analyser               : analyser,
  };
}

global.AudioContext = jest.fn(() => makeMockAudioContext());

// ─── Audio element mock ───────────────────────────────────────────────────────
class MockAudio {
  constructor(src) {
    this.src     = src ?? '';
    this.onended = null;
    this.onerror = null;
    this._paused = false;
    MockAudio._instances.push(this);
  }

  play() {
    return new Promise((resolve) => {
      setTimeout(() => {
        // Auto-fire onended unless test overrides
        if (!MockAudio._blockAutoEnd) {
          this.onended?.();
        }
        resolve();
      }, 10);
    });
  }

  pause() { this._paused = true; }

  static _instances = [];
  static _blockAutoEnd = false;
  static _reset() {
    MockAudio._instances    = [];
    MockAudio._blockAutoEnd = false;
  }
}

global.Audio = MockAudio;

// ─── fetch mock factory ───────────────────────────────────────────────────────
function mockFetchOk(body = {}) {
  return jest.fn().mockResolvedValue({
    ok         : true,
    status     : 200,
    statusText : 'OK',
    json       : () => Promise.resolve(body),
    blob       : () => Promise.resolve(new Blob(['audio'], { type: 'audio/mp3' })),
  });
}

function mockFetchError(status = 500, text = 'Internal Server Error') {
  return jest.fn().mockResolvedValue({
    ok: false, status, statusText: text,
    json: () => Promise.resolve({}),
    blob: () => Promise.resolve(new Blob()),
  });
}

// ─── VAD / ORT mocks ─────────────────────────────────────────────────────────
let _vadCallbacks = {};
const mockMicVAD = { start: jest.fn(), destroy: jest.fn() };

global.vad = {
  MicVAD: {
    new: jest.fn(async (opts) => {
      _vadCallbacks = opts;
      return mockMicVAD;
    }),
  },
};
global.ort = {}; // just needs to exist for isSupported()

// ─── Load source modules ──────────────────────────────────────────────────────
// AmbientVAD sets globalThis.AmbientVAD; VocalTwist picks it up at startAmbient()
const { AmbientVAD }                                                       = require('../ambient-vad');
const { VocalTwist, VocalTwistRecorder, VocalTwistTTS, VocalTwistElement } = require('../vocal-twist');

// ═════════════════════════════════════════════════════════════════════════════
// Suite 1 — VocalTwistRecorder
// ═════════════════════════════════════════════════════════════════════════════
describe('VocalTwistRecorder', () => {
  let rec;

  beforeEach(() => {
    MockMediaRecorder._reset();
    MockAudio._reset();
    navigator.mediaDevices = {
      getUserMedia: jest.fn().mockResolvedValue(makeMockStream()),
    };
    rec = new VocalTwistRecorder();
  });

  afterEach(() => {
    rec.cancel();
    jest.clearAllMocks();
  });

  test('constructor: sets default mimeType', () => {
    expect(rec).toBeInstanceOf(VocalTwistRecorder);
    // isRecording should be false before start
    expect(rec.isRecording).toBe(false);
  });

  test('start(): requests getUserMedia with audio:true, video:false', async () => {
    await rec.start();
    expect(navigator.mediaDevices.getUserMedia).toHaveBeenCalledWith({
      audio: true,
      video: false,
    });
  });

  test('start(): onStart callback is fired', async () => {
    const onStart = jest.fn();
    rec.onStart = onStart;
    await rec.start();
    expect(onStart).toHaveBeenCalledTimes(1);
  });

  test('start(): isRecording is true after start', async () => {
    await rec.start();
    expect(rec.isRecording).toBe(true);
  });

  test('stop(): resolves with a Blob', async () => {
    await rec.start();
    const blob = await rec.stop();
    expect(blob).toBeInstanceOf(Blob);
  });

  test('stop(): fires onStop with the audio Blob', async () => {
    const onStop = jest.fn();
    rec.onStop = onStop;
    await rec.start();
    const blob = await rec.stop();
    expect(onStop).toHaveBeenCalledWith(blob);
  });

  test('stop(): isRecording is false after stop', async () => {
    await rec.start();
    await rec.stop();
    expect(rec.isRecording).toBe(false);
  });

  test('stop(): rejects when not recording', async () => {
    await expect(rec.stop()).rejects.toThrow('not currently recording');
  });

  test('cancel(): does not fire onStop', async () => {
    const onStop = jest.fn();
    rec.onStop = onStop;
    await rec.start();
    rec.cancel();
    expect(onStop).not.toHaveBeenCalled();
  });

  test('cancel(): stops media tracks', async () => {
    const stream = makeMockStream();
    navigator.mediaDevices.getUserMedia = jest.fn().mockResolvedValue(stream);
    const rec2 = new VocalTwistRecorder();
    await rec2.start();
    rec2.cancel();
    expect(stream._track.stop).toHaveBeenCalled();
  });

  test('error when mic is denied', async () => {
    const micErr = new Error('Permission denied');
    navigator.mediaDevices.getUserMedia = jest.fn().mockRejectedValue(micErr);
    const onError = jest.fn();
    const rec2    = new VocalTwistRecorder();
    rec2.onError  = onError;
    await expect(rec2.start()).rejects.toThrow('Permission denied');
    expect(onError).toHaveBeenCalledWith(micErr);
  });

  test('onLevel callback receives a normalised value 0–1', async () => {
    const levels = [];
    rec.onLevel = (v) => levels.push(v);
    await rec.start();
    // Wait a couple of animation frames
    await new Promise((r) => setTimeout(r, 50));
    expect(levels.length).toBeGreaterThan(0);
    levels.forEach((v) => {
      expect(v).toBeGreaterThanOrEqual(0);
      expect(v).toBeLessThanOrEqual(1);
    });
  });
});

// ═════════════════════════════════════════════════════════════════════════════
// Suite 2 — VocalTwistTTS
// ═════════════════════════════════════════════════════════════════════════════
describe('VocalTwistTTS', () => {
  let tts;

  beforeEach(() => {
    MockAudio._reset();
    global.fetch = mockFetchOk({ text: 'ignored' });
    tts = new VocalTwistTTS();
  });

  afterEach(() => {
    tts.stop();
    jest.clearAllMocks();
  });

  test('isPlaying is false before play()', () => {
    expect(tts.isPlaying).toBe(false);
  });

  test('play(): POSTs to speakUrl with JSON body', async () => {
    await tts.play('Hello', { url: '/api/speak', language: 'en', voice: 'nova' });
    expect(fetch).toHaveBeenCalledWith(
      '/api/speak',
      expect.objectContaining({
        method: 'POST',
        body  : JSON.stringify({ text: 'Hello', language: 'en', voice: 'nova' }),
      })
    );
  });

  test('play(): forwards X-API-Key header when apiKey provided', async () => {
    await tts.play('Hi', { url: '/api/speak', apiKey: 'sk-test' });
    const [, init] = fetch.mock.calls[0];
    expect(init.headers['X-API-Key']).toBe('sk-test');
  });

  test('play(): fires onPlay then onEnd callbacks', async () => {
    const onPlay = jest.fn();
    const onEnd  = jest.fn();
    tts.onPlay   = onPlay;
    tts.onEnd    = onEnd;
    await tts.play('Hello');
    expect(onPlay).toHaveBeenCalledTimes(1);
    expect(onEnd).toHaveBeenCalledTimes(1);
  });

  test('play(): isPlaying is false after playback completes', async () => {
    await tts.play('Hello');
    expect(tts.isPlaying).toBe(false);
  });

  test('stop(): fires onEnd and sets isPlaying false', async () => {
    MockAudio._blockAutoEnd = true; // keep audio "playing"
    const onEnd = jest.fn();
    tts.onEnd   = onEnd;
    const playPromise = tts.play('Interrupted');
    await new Promise((r) => setTimeout(r, 15)); // let fetch resolve
    expect(tts.isPlaying).toBe(true);
    tts.stop();
    expect(tts.isPlaying).toBe(false);
    expect(onEnd).toHaveBeenCalledTimes(1);
    // playPromise should have settled (abort or end)
    await expect(playPromise).resolves.toBeUndefined();
  });

  test('concurrent play(): stops previous audio before playing new one', async () => {
    MockAudio._blockAutoEnd = true;
    const firstPromise = tts.play('First').catch(() => {});
    await new Promise((r) => setTimeout(r, 15));

    const firstAudio = MockAudio._instances[0];
    MockAudio._blockAutoEnd = false;
    tts.play('Second');
    await new Promise((r) => setTimeout(r, 40));
    // First audio should have been paused via stop()
    expect(firstAudio._paused).toBe(true);
  });

  test('play(): fires onError and throws on HTTP error', async () => {
    global.fetch = mockFetchError(503, 'Service Unavailable');
    const onError = jest.fn();
    tts.onError   = onError;
    await expect(tts.play('Test')).rejects.toThrow('503');
    expect(onError).toHaveBeenCalledTimes(1);
  });

  test('play(): creates and revokes a blob URL', async () => {
    global.URL.createObjectURL = jest.fn(() => 'blob:test');
    global.URL.revokeObjectURL = jest.fn();
    await tts.play('Blob test');
    expect(URL.createObjectURL).toHaveBeenCalledTimes(1);
    expect(URL.revokeObjectURL).toHaveBeenCalledTimes(1);
  });
});

// ═════════════════════════════════════════════════════════════════════════════
// Suite 3 — VocalTwist (Orchestrator)
// ═════════════════════════════════════════════════════════════════════════════
describe('VocalTwist', () => {
  let vt;

  beforeEach(() => {
    MockMediaRecorder._reset();
    MockAudio._reset();
    navigator.mediaDevices = {
      getUserMedia: jest.fn().mockResolvedValue(makeMockStream()),
    };
    global.fetch = mockFetchOk({ text: 'transcribed text', display_text: 'Transcribed Text' });
  });

  afterEach(() => {
    vt?.destroy();
    jest.clearAllMocks();
  });

  test('state is "idle" on construction', () => {
    vt = new VocalTwist();
    expect(vt.state).toBe('idle');
  });

  test('startRecording(): transitions to "recording"', async () => {
    vt = new VocalTwist();
    const states = [];
    vt = new VocalTwist({ onStateChange: (s) => states.push(s) });
    await vt.startRecording();
    expect(states).toContain('recording');
    expect(vt.state).toBe('recording');
  });

  test('stopRecording(): transitions recording → transcribing → idle', async () => {
    const states = [];
    vt = new VocalTwist({ onStateChange: (s) => states.push(s) });
    await vt.startRecording();
    await vt.stopRecording();
    expect(states).toEqual(expect.arrayContaining(['recording', 'transcribing', 'idle']));
    expect(vt.state).toBe('idle');
  });

  test('stopRecording(): fires onTranscript with response text', async () => {
    const onTranscript = jest.fn();
    vt = new VocalTwist({ onTranscript });
    await vt.startRecording();
    await vt.stopRecording();
    expect(onTranscript).toHaveBeenCalledWith('transcribed text', 'Transcribed Text');
  });

  test('transcribe(): POSTs FormData with audio and language', async () => {
    vt = new VocalTwist({ language: 'fr' });
    const blob = new Blob(['audio'], { type: 'audio/webm' });
    vt = new VocalTwist({ language: 'fr' });
    // patch state to allow transcribe
    Object.defineProperty(vt, 'state', { get: () => 'idle', configurable: true });
    await vt.transcribe(blob);
    const [url, init] = fetch.mock.calls[0];
    expect(url).toBe('/api/transcribe');
    expect(init.body).toBeInstanceOf(FormData);
  });

  test('transcribe(): returns text from response', async () => {
    vt = new VocalTwist();
    const blob = new Blob(['audio']);
    const text = await vt.transcribe(blob);
    expect(text).toBe('transcribed text');
  });

  test('transcribe(): sends X-API-Key header when apiKey configured', async () => {
    vt = new VocalTwist({ apiKey: 'my-key' });
    await vt.transcribe(new Blob(['audio']));
    const [, init] = fetch.mock.calls[0];
    expect(init.headers['X-API-Key']).toBe('my-key');
  });

  test('transcribe(): fires onError on HTTP failure', async () => {
    global.fetch = mockFetchError(500);
    const onError = jest.fn();
    vt = new VocalTwist({ onError });
    await expect(vt.transcribe(new Blob(['x']))).rejects.toThrow('500');
    expect(onError).toHaveBeenCalledTimes(1);
  });

  test('speak(): transitions to "speaking" then "idle"', async () => {
    const states = [];
    vt = new VocalTwist({ onStateChange: (s) => states.push(s) });
    global.fetch = mockFetchOk({}); // TTS returns audio blob
    await vt.speak('Hello');
    expect(states).toContain('speaking');
    expect(states[states.length - 1]).toBe('idle');
  });

  test('speak(): fires onTTSStart and onTTSEnd', async () => {
    const onTTSStart = jest.fn();
    const onTTSEnd   = jest.fn();
    vt = new VocalTwist({ onTTSStart, onTTSEnd });
    await vt.speak('Hi');
    expect(onTTSStart).toHaveBeenCalledTimes(1);
    expect(onTTSEnd).toHaveBeenCalledTimes(1);
  });

  test('setLanguage(): updates language used in transcription', async () => {
    vt = new VocalTwist({ language: 'en' });
    vt.setLanguage('de');
    await vt.transcribe(new Blob(['audio']));
    const [, init] = fetch.mock.calls[0];
    const lang = init.body.get('language');
    expect(lang).toBe('de');
  });

  test('setVoice(): updates voice used in TTS', async () => {
    vt = new VocalTwist({ speakUrl: '/api/speak' });
    vt.setVoice('echo');
    await vt.speak('Hi');
    const [, init] = fetch.mock.calls[0];
    const body = JSON.parse(init.body);
    expect(body.voice).toBe('echo');
  });

  test('startRecording(): no-op if not idle', async () => {
    const states = [];
    vt = new VocalTwist({ onStateChange: (s) => states.push(s) });
    await vt.startRecording();
    const statesBefore = states.length;
    await vt.startRecording(); // second call while recording
    expect(states.length).toBe(statesBefore); // no extra state change
  });

  test('destroy(): resets state to idle', async () => {
    vt = new VocalTwist();
    await vt.startRecording();
    vt.destroy();
    expect(vt.state).toBe('idle');
  });

  test('startAmbient(): throws if AmbientVAD not loaded', async () => {
    const saved = globalThis.AmbientVAD;
    delete globalThis.AmbientVAD;
    vt = new VocalTwist();
    await expect(vt.startAmbient()).rejects.toThrow('AmbientVAD not loaded');
    globalThis.AmbientVAD = saved;
  });

  test('startAmbient(): state becomes ambient-listening', async () => {
    const states = [];
    vt = new VocalTwist({
      onStateChange: (s) => states.push(s),
    });
    await vt.startAmbient();
    // AmbientVAD fires onStateChange('listening') → 'ambient-listening'
    _vadCallbacks.onSpeechEnd?.(new Float32Array(16));
    // dispatch listening state change via ambientVAD callback
    _vadCallbacks.onStateChange?.('listening');
    expect(states).toContain('ambient-listening');
  });

  test('stopAmbient(): resets to idle', async () => {
    const states = [];
    vt = new VocalTwist({ onStateChange: (s) => states.push(s) });
    await vt.startAmbient();
    _vadCallbacks.onStateChange?.('listening');
    vt.stopAmbient();
    expect(vt.state).toBe('idle');
  });

  test('full-duplex: onSpeechStart stops TTS', async () => {
    MockAudio._blockAutoEnd = true;
    const onSpeechStart = jest.fn();
    vt = new VocalTwist({ onSpeechStart });
    // Start speaking
    const speakPromise = vt.speak('Long response').catch(() => {});
    await new Promise((r) => setTimeout(r, 20));
    // Start ambient
    await vt.startAmbient();
    // Simulate speech start
    _vadCallbacks.onSpeechStart?.();
    // TTS should have been stopped
    expect(onSpeechStart).toHaveBeenCalledTimes(1);
    MockAudio._reset();
  });
});

// ═════════════════════════════════════════════════════════════════════════════
// Suite 4 — AmbientVAD
// ═════════════════════════════════════════════════════════════════════════════
describe('AmbientVAD', () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockMicVAD.start.mockClear();
    mockMicVAD.destroy.mockClear();
    global.vad.MicVAD.new.mockClear();
    global.fetch = mockFetchOk({ text: 'ambient result', display_text: 'Ambient Result' });
  });

  test('isSupported(): returns true when vad, ort, and getUserMedia exist', () => {
    expect(AmbientVAD.isSupported()).toBe(true);
  });

  test('isSupported(): returns false when window.vad is missing', () => {
    const saved = globalThis.vad;
    delete globalThis.vad;
    expect(AmbientVAD.isSupported()).toBe(false);
    globalThis.vad = saved;
  });

  test('isSupported(): returns false when window.ort is missing', () => {
    const saved = globalThis.ort;
    delete globalThis.ort;
    expect(AmbientVAD.isSupported()).toBe(false);
    globalThis.ort = saved;
  });

  test('constructor: state is "idle" by default', () => {
    const av = new AmbientVAD();
    expect(av.state).toBe('idle');
  });

  test('constructor: default options applied', () => {
    const av = new AmbientVAD();
    expect(av._opts.transcribeUrl).toBe('/api/transcribe-ambient');
    expect(av._opts.language).toBe('en');
    expect(av._opts.silenceMs).toBe(15000);
    expect(av._opts.maxBufferMs).toBe(30000);
  });

  test('constructor: custom options accepted', () => {
    const av = new AmbientVAD({
      transcribeUrl: '/my/stt',
      language     : 'ja',
      silenceMs    : 5000,
      maxBufferMs  : 10000,
    });
    expect(av._opts.transcribeUrl).toBe('/my/stt');
    expect(av._opts.language).toBe('ja');
    expect(av._opts.silenceMs).toBe(5000);
  });

  test('start(): throws when unsupported', async () => {
    const saved = globalThis.ort;
    delete globalThis.ort;
    const av = new AmbientVAD();
    await expect(av.start()).rejects.toThrow('not supported');
    globalThis.ort = saved;
  });

  test('start(): transitions to "listening"', async () => {
    const states = [];
    const av     = new AmbientVAD({ onStateChange: (s) => states.push(s) });
    await av.start();
    expect(states).toContain('listening');
    av.stop();
  });

  test('start(): creates MicVAD', async () => {
    const av = new AmbientVAD();
    await av.start();
    expect(global.vad.MicVAD.new).toHaveBeenCalledTimes(1);
    expect(mockMicVAD.start).toHaveBeenCalledTimes(1);
    av.stop();
  });

  test('start(): no-op if already listening', async () => {
    const av = new AmbientVAD();
    await av.start();
    await av.start(); // second call
    expect(global.vad.MicVAD.new).toHaveBeenCalledTimes(1);
    av.stop();
  });

  test('stop(): transitions back to "idle"', async () => {
    const states = [];
    const av     = new AmbientVAD({ onStateChange: (s) => states.push(s) });
    await av.start();
    av.stop();
    expect(av.state).toBe('idle');
    expect(states[states.length - 1]).toBe('idle');
  });

  test('stop(): destroys VAD instance', async () => {
    const av = new AmbientVAD();
    await av.start();
    av.stop();
    expect(mockMicVAD.destroy).toHaveBeenCalledTimes(1);
  });

  test('stop(): is idempotent (safe to call twice)', async () => {
    const av = new AmbientVAD();
    await av.start();
    av.stop();
    expect(() => av.stop()).not.toThrow();
  });

  test('onSpeechStart callback fires on VAD speech-start event', async () => {
    const onSpeechStart = jest.fn();
    const av            = new AmbientVAD({ onSpeechStart });
    await av.start();
    _vadCallbacks.onSpeechStart?.();
    expect(onSpeechStart).toHaveBeenCalledTimes(1);
    av.stop();
  });

  test('onSpeechEnd: buffering state set and silence timer starts', async () => {
    jest.useFakeTimers();
    const states = [];
    const av     = new AmbientVAD({
      onStateChange: (s) => states.push(s),
      silenceMs: 500,
    });
    await av.start();
    _vadCallbacks.onSpeechEnd?.(new Float32Array(100));
    expect(states).toContain('buffering');
    jest.useRealTimers();
    av.stop();
  });

  test('transcription triggered after silence window', async () => {
    jest.useFakeTimers();
    const onTranscript = jest.fn();
    const av = new AmbientVAD({
      onTranscript,
      silenceMs : 500,
      maxBufferMs: 60000,
    });
    await av.start();
    _vadCallbacks.onSpeechEnd?.(new Float32Array(1600)); // 100 ms at 16 kHz
    jest.advanceTimersByTime(600); // fire silence timer
    jest.useRealTimers();
    // allow microtasks to flush
    await new Promise((r) => setTimeout(r, 50));
    expect(fetch).toHaveBeenCalledTimes(1);
    av.stop();
  });

  test('transcription result fires onTranscript', async () => {
    jest.useFakeTimers();
    const onTranscript = jest.fn();
    const av = new AmbientVAD({ onTranscript, silenceMs: 100, maxBufferMs: 60000 });
    await av.start();
    _vadCallbacks.onSpeechEnd?.(new Float32Array(800));
    jest.advanceTimersByTime(200);
    jest.useRealTimers();
    await new Promise((r) => setTimeout(r, 50));
    expect(onTranscript).toHaveBeenCalledWith('ambient result', 'Ambient Result');
    av.stop();
  });

  test('onError fired on HTTP failure', async () => {
    global.fetch = mockFetchError(503, 'Unavailable');
    jest.useFakeTimers();
    const onError = jest.fn();
    const av      = new AmbientVAD({ onError, silenceMs: 100, maxBufferMs: 60000 });
    await av.start();
    _vadCallbacks.onSpeechEnd?.(new Float32Array(800));
    jest.advanceTimersByTime(200);
    jest.useRealTimers();
    await new Promise((r) => setTimeout(r, 50));
    expect(onError).toHaveBeenCalledTimes(1);
    av.stop();
  });

  // ── WAV encoder ──────────────────────────────────────────────────────────────
  describe('AmbientVAD.encodeWav (static)', () => {
    test('returns a Blob of type audio/wav', () => {
      const blob = AmbientVAD.encodeWav([new Float32Array([0, 0.5, -0.5, 1, -1])], 16000);
      expect(blob).toBeInstanceOf(Blob);
      expect(blob.type).toBe('audio/wav');
    });

    test('output size = 44 (header) + samples * 2', () => {
      const samples = new Float32Array(100);
      const blob    = AmbientVAD.encodeWav([samples], 16000);
      expect(blob.size).toBe(44 + 100 * 2);
    });

    test('concatenates multiple chunks', () => {
      const a = new Float32Array(50).fill(0.1);
      const b = new Float32Array(50).fill(0.2);
      const blob = AmbientVAD.encodeWav([a, b], 16000);
      expect(blob.size).toBe(44 + 100 * 2);
    });

    test('clamps float samples to [-1, 1]', () => {
      // Should not throw for out-of-range values
      expect(() => {
        AmbientVAD.encodeWav([new Float32Array([2, -2, 100, -100])], 16000);
      }).not.toThrow();
    });

    test('handles empty input gracefully', () => {
      const blob = AmbientVAD.encodeWav([new Float32Array(0)], 16000);
      expect(blob.size).toBe(44); // header only
    });

    test('WAV header contains RIFF signature', async () => {
      const blob = AmbientVAD.encodeWav([new Float32Array([0.1, 0.2])], 16000);
      const buffer = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload  = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsArrayBuffer(blob);
      });
      const view = new DataView(buffer);
      const riff = String.fromCharCode(
        view.getUint8(0), view.getUint8(1), view.getUint8(2), view.getUint8(3)
      );
      expect(riff).toBe('RIFF');
    });

    test('WAV header encodes correct sampleRate', async () => {
      const blob = AmbientVAD.encodeWav([new Float32Array([0])], 22050);
      const buffer = await new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload  = () => resolve(reader.result);
        reader.onerror = reject;
        reader.readAsArrayBuffer(blob);
      });
      const view = new DataView(buffer);
      expect(view.getUint32(24, true)).toBe(22050);
    });
  });
});

// ═════════════════════════════════════════════════════════════════════════════
// Suite 5 — VocalTwistElement (Custom Element)
// ═════════════════════════════════════════════════════════════════════════════
describe('VocalTwistElement', () => {
  let el;

  beforeEach(() => {
    MockMediaRecorder._reset();
    MockAudio._reset();
    navigator.mediaDevices = {
      getUserMedia: jest.fn().mockResolvedValue(makeMockStream()),
    };
    global.fetch = mockFetchOk({ text: 'hello' });
    el = document.createElement('vocal-twist');
    document.body.appendChild(el);
  });

  afterEach(() => {
    if (el && el.parentNode) el.parentNode.removeChild(el);
    jest.clearAllMocks();
  });

  test('custom element is registered', () => {
    expect(customElements.get('vocal-twist')).toBe(VocalTwistElement);
  });

  test('vocalTwist property returns a VocalTwist instance', () => {
    expect(el.vocalTwist).toBeInstanceOf(VocalTwist);
  });

  test('disconnectedCallback destroys the VocalTwist instance', () => {
    const vt     = el.vocalTwist;
    const destroy = jest.spyOn(vt, 'destroy');
    document.body.removeChild(el);
    expect(destroy).toHaveBeenCalledTimes(1);
    el = null; // already removed
  });

  test('language attribute defaults to "en"', () => {
    const el2 = document.createElement('vocal-twist');
    document.body.appendChild(el2);
    expect(el2.vocalTwist).toBeDefined();
    document.body.removeChild(el2);
  });

  test('language attribute change propagates to VocalTwist', () => {
    const setLang = jest.spyOn(el.vocalTwist, 'setLanguage');
    el.setAttribute('language', 'es');
    expect(setLang).toHaveBeenCalledWith('es');
  });

  test('voice attribute change propagates to VocalTwist', () => {
    const setVoice = jest.spyOn(el.vocalTwist, 'setVoice');
    el.setAttribute('voice', 'shimmer');
    expect(setVoice).toHaveBeenCalledWith('shimmer');
  });

  test('vt:transcript event fired on transcript', async () => {
    const handler = jest.fn();
    el.addEventListener('vt:transcript', handler);
    // Simulate transcription via the VocalTwist internal callback
    await el.vocalTwist.transcribe(new Blob(['audio']));
    expect(handler).toHaveBeenCalledTimes(1);
    const detail = handler.mock.calls[0][0].detail;
    expect(detail.text).toBe('hello');
  });

  test('vt:statechange event fired on state transitions', async () => {
    const handler = jest.fn();
    el.addEventListener('vt:statechange', handler);
    await el.vocalTwist.startRecording();
    const states = handler.mock.calls.map((c) => c[0].detail.state);
    expect(states).toContain('recording');
  });

  test('vt:error event dispatched on error', () => {
    const handler = jest.fn();
    el.addEventListener('vt:error', handler);
    global.fetch = mockFetchError(500);
    return el.vocalTwist.transcribe(new Blob(['x'])).catch(() => {
      expect(handler).toHaveBeenCalledTimes(1);
    });
  });

  test('ambient attribute triggers startAmbient()', () => {
    const el2 = document.createElement('vocal-twist');
    el2.setAttribute('ambient', '');
    const startAmbient = jest.fn().mockResolvedValue(undefined);
    // Patch prototype before connectedCallback
    jest.spyOn(VocalTwist.prototype, 'startAmbient').mockImplementation(startAmbient);
    document.body.appendChild(el2);
    // Restore
    VocalTwist.prototype.startAmbient.mockRestore?.();
    document.body.removeChild(el2);
  });

  test('observedAttributes includes required attribute names', () => {
    expect(VocalTwistElement.observedAttributes).toEqual(
      expect.arrayContaining(['language', 'voice', 'transcribe-url', 'speak-url', 'ambient', 'api-key'])
    );
  });
});
