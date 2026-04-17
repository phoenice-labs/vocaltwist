/**
 * content/focus-watcher.js — Text Input Focus Detection
 *
 * Watches for the user focusing on any text input across the entire DOM,
 * including contenteditable elements used by ChatGPT, Claude, Notion, etc.
 * Fires onFocus/onBlur callbacks so the mic button can appear/disappear.
 */

'use strict';

const focusWatcher = (() => {
  let _onFocus = null;
  let _onBlur  = null;
  let _active  = false;
  let _current = null;

  /**
   * Determine if an element is a text input we should attach to.
   * @param {Element} el
   * @returns {boolean}
   */
  function isTextInput(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE) return false;
    if (el.tagName === 'TEXTAREA') return true;
    if (el.tagName === 'INPUT') {
      const t = (el.type || '').toLowerCase();
      return ['text', 'search', 'email', 'url', 'tel', ''].includes(t);
    }
    if (el.isContentEditable) return true;
    return false;
  }

  function onFocusIn(e) {
    const el = e.target;
    if (!isTextInput(el)) return;
    _current = el;
    _onFocus?.(el);
  }

  function onFocusOut(e) {
    // Grace period — user may be clicking the mic button
    setTimeout(() => {
      // If focus moved outside our input and the mic button, fire blur
      const active = document.activeElement;
      const micEl  = document.getElementById('vt-mic-button');
      if (
        active !== _current &&
        (!micEl || !micEl.contains(active))
      ) {
        _current = null;
        _onBlur?.();
      }
    }, MIC_DETACH_GRACE_MS ?? 200);
  }

  return {
    start(onFocusCallback, onBlurCallback) {
      if (_active) return;
      _active  = true;
      _onFocus = onFocusCallback;
      _onBlur  = onBlurCallback;
      document.addEventListener('focusin',  onFocusIn,  true);
      document.addEventListener('focusout', onFocusOut, true);
    },

    stop() {
      _active = false;
      document.removeEventListener('focusin',  onFocusIn,  true);
      document.removeEventListener('focusout', onFocusOut, true);
    },

    /** @returns {Element|null} Currently focused input, if any. */
    currentInput() {
      return _current;
    },

    isTextInput,
  };
})();

// Expose for use by content.js (same execution context)
window.__vtFocusWatcher = focusWatcher;
