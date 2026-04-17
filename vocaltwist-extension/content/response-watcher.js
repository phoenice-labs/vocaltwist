/**
 * content/response-watcher.js — AI Response Detection & TTS Trigger
 *
 * Three-tier detection strategy:
 *  Tier 1 — Site registry (exact CSS selectors for known apps)
 *  Tier 2 — Heuristic detection (MutationObserver + text heuristics)
 *  Tier 3 — User-defined selector (from settings.customSelectors)
 *
 * Streaming completion detection:
 *  - Site-specific: watch for streaming indicator to disappear
 *  - Generic: text stops changing for TTS_DEBOUNCE_MS (1.5s)
 */

'use strict';

const responseWatcher = (() => {
  let _observer       = null;
  let _onResponse     = null;
  let _siteConfig     = null;
  let _customSelector = null;
  let _debounceTimer  = null;
  let _lastText       = '';
  let _started        = false;

  // Nodes we have already attached TTS controls to (avoid duplicates)
  const _seen = new WeakSet();

  // ─── Site registry (loaded via fetch from selectors/site-registry.json) ──────

  let _registry = null;

  async function loadRegistry() {
    if (_registry) return;
    try {
      const url  = chrome.runtime.getURL('selectors/site-registry.json');
      const res  = await fetch(url);
      _registry  = await res.json();
    } catch (_) {
      _registry = {};
    }
  }

  function getSiteConfig(hostname) {
    if (!_registry) return null;
    // Try exact match, then strip 'www.'
    return (
      _registry[hostname] ||
      _registry[hostname.replace(/^www\./, '')] ||
      null
    );
  }

  // ─── Heuristic: detect AI response containers ─────────────────────────────────

  function isLikelyAIResponse(node) {
    if (node.nodeType !== Node.ELEMENT_NODE) return false;
    const text = node.innerText || node.textContent || '';
    const wordCount = text.trim().split(/\s+/).length;
    if (wordCount < 20) return false;

    // Must not be inside an input area
    if (node.closest('textarea, input, [contenteditable="true"]')) return false;

    // Must be inside a scrollable container (chat UI pattern)
    let el = node.parentElement;
    while (el && el !== document.body) {
      const style = getComputedStyle(el);
      if (style.overflow === 'auto' || style.overflow === 'scroll' ||
          style.overflowY === 'auto' || style.overflowY === 'scroll') {
        return true;
      }
      el = el.parentElement;
    }
    return false;
  }

  // ─── Check if streaming is in progress ────────────────────────────────────────

  function isStreaming() {
    if (!_siteConfig?.streamingIndicator) return false;
    return !!document.querySelector(_siteConfig.streamingIndicator);
  }

  // ─── Extract clean text from a response node ──────────────────────────────────

  function extractText(node) {
    // Remove hidden elements, code blocks raw content remains readable
    return (node.innerText || node.textContent || '').trim();
  }

  // ─── Trigger TTS with debounce ────────────────────────────────────────────────

  function triggerDebounced(node) {
    clearTimeout(_debounceTimer);
    _debounceTimer = setTimeout(() => {
      if (isStreaming()) {
        // Still streaming — wait longer
        triggerDebounced(node);
        return;
      }
      const text = extractText(node);
      if (text && text !== _lastText && text.trim().split(/\s+/).length >= 5) {
        _lastText = text;
        injectTTSControls(node, text);
        _onResponse?.(text);
      }
    }, TTS_DEBOUNCE_MS ?? 1500);
  }

  // ─── TTS Controls (mute / replay buttons) ────────────────────────────────────

  function injectTTSControls(node, text) {
    if (_seen.has(node)) return;
    _seen.add(node);

    const controls = document.createElement('span');
    controls.className = 'vt-tts-controls';
    controls.setAttribute('aria-label', 'VocalTwist voice controls');

    const muteBtn = document.createElement('button');
    muteBtn.className = 'vt-tts-btn';
    muteBtn.title     = 'Mute / stop speech';
    muteBtn.innerHTML = '🔇';
    muteBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      chrome.runtime.sendMessage({ type: 'STOP_SPEAKING' });
    });

    const replayBtn = document.createElement('button');
    replayBtn.className = 'vt-tts-btn';
    replayBtn.title     = 'Replay response';
    replayBtn.innerHTML = '🔊';
    replayBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      _onResponse?.(extractText(node));
    });

    controls.appendChild(muteBtn);
    controls.appendChild(replayBtn);
    node.appendChild(controls);
  }

  // ─── MutationObserver callback ────────────────────────────────────────────────

  function handleMutations(mutations) {
    for (const mutation of mutations) {
      // Check added nodes (new response containers)
      for (const added of mutation.addedNodes) {
        if (added.nodeType !== Node.ELEMENT_NODE) continue;
        checkNode(added);
        // Also check descendants
        added.querySelectorAll?.('*').forEach(checkNode);
      }

      // Check target for text changes
      if (mutation.type === 'characterData' || mutation.type === 'childList') {
        checkNode(mutation.target.nodeType === Node.ELEMENT_NODE
          ? mutation.target
          : mutation.target.parentElement);
      }
    }
  }

  function checkNode(node) {
    if (!node || node.nodeType !== Node.ELEMENT_NODE) return;

    // Tier 3 — user-defined selector
    if (_customSelector) {
      const match = node.closest?.(_customSelector) || (node.matches?.(_customSelector) ? node : null);
      if (match && !_seen.has(match)) {
        triggerDebounced(match);
        return;
      }
    }

    // Tier 1 — site registry
    if (_siteConfig?.responseSelector) {
      const match = node.closest?.(_siteConfig.responseSelector) ||
                    (node.matches?.(_siteConfig.responseSelector) ? node : null);
      if (match && !_seen.has(match)) {
        triggerDebounced(match);
        return;
      }
    }

    // Tier 2 — heuristic
    if (isLikelyAIResponse(node) && !_seen.has(node)) {
      triggerDebounced(node);
    }
  }

  // ─── Public API ───────────────────────────────────────────────────────────────

  return {
    async start(onResponseCallback, customSelector) {
      await loadRegistry();
      _onResponse     = onResponseCallback;
      _siteConfig     = getSiteConfig(location.hostname);
      _customSelector = customSelector || null;
      _started        = true;

      if (_observer) _observer.disconnect();
      _observer = new MutationObserver(handleMutations);
      _observer.observe(document.body, {
        childList:     true,
        subtree:       true,
        characterData: true,
      });
    },

    stop() {
      _observer?.disconnect();
      _observer  = null;
      _started   = false;
      clearTimeout(_debounceTimer);
    },

    restart(onResponseCallback, customSelector) {
      this.stop();
      this.start(onResponseCallback, customSelector);
    },

    get isStarted() { return _started; },
  };
})();

window.__vtResponseWatcher = responseWatcher;
