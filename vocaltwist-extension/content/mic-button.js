/**
 * content/mic-button.js — Floating Mic Button UI
 *
 * Renders a small mic button near the focused text input.
 * States: idle | recording | processing | error
 *
 * Also handles text injection into any input type, including
 * React/Vue-controlled inputs (using native event setter pattern).
 */

'use strict';

const micButton = (() => {
  let _btn        = null;
  let _target     = null;
  let _state      = 'idle';
  let _onClick    = null;

  // ─── Create the button element ───────────────────────────────────────────────

  function create() {
    if (_btn) return;

    _btn = document.createElement('button');
    _btn.id               = 'vt-mic-button';
    _btn.setAttribute('aria-label', 'Toggle voice input (Ctrl+Shift+V)');
    _btn.setAttribute('title', 'VocalTwist — click to record (Ctrl+Shift+V)');
    _btn.setAttribute('data-state', 'idle');
    _btn.className        = 'vt-mic-btn vt-state-idle';
    _btn.innerHTML        = _iconSVG('idle');

    _btn.addEventListener('click', (e) => {
      e.preventDefault();
      e.stopPropagation();
      _onClick?.(_state);
    });

    // Prevent the button from stealing focus from the input
    _btn.addEventListener('mousedown', (e) => e.preventDefault());

    document.body.appendChild(_btn);
  }

  function destroy() {
    if (_btn) {
      _btn.remove();
      _btn = null;
    }
    _target = null;
    _state  = 'idle';
  }

  // ─── Positioning ─────────────────────────────────────────────────────────────

  function attachTo(inputEl) {
    if (!inputEl) return;
    _target = inputEl;
    if (!_btn) create();
    _btn.style.display = 'flex';
    reposition();
  }

  function detach() {
    if (_btn) _btn.style.display = 'none';
    _target = null;
  }

  function reposition() {
    if (!_btn || !_target) return;
    const rect = _target.getBoundingClientRect();
    const scrollX = window.scrollX;
    const scrollY = window.scrollY;

    // Position at bottom-right of the input, outside the border
    _btn.style.position = 'absolute';
    _btn.style.left     = `${rect.right  + scrollX + 6}px`;
    _btn.style.top      = `${rect.bottom + scrollY - 36}px`;
    _btn.style.zIndex   = '2147483647';
  }

  // ─── State management ─────────────────────────────────────────────────────────

  function setState(newState) {
    if (!_btn) return;
    _state = newState;
    _btn.setAttribute('data-state', newState);
    _btn.className  = `vt-mic-btn vt-state-${newState}`;
    _btn.innerHTML  = _iconSVG(newState);

    const labels = {
      idle:       'Click to start recording (Ctrl+Shift+V)',
      recording:  'Recording… click to stop',
      processing: 'Processing audio…',
      error:      'VocalTwist error — click to retry',
    };
    _btn.setAttribute('aria-label', labels[newState] || labels.idle);
  }

  // ─── SVG Icons ───────────────────────────────────────────────────────────────

  function _iconSVG(state) {
    switch (state) {
      case 'recording':
        return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" width="18" height="18">
          <path d="M12 15c1.66 0 3-1.34 3-3V6c0-1.66-1.34-3-3-3S9 4.34 9 6v6c0 1.66 1.34 3 3 3zm-1-9c0-.55.45-1 1-1s1 .45 1 1v6c0 .55-.45 1-1 1s-1-.45-1-1V6z"/>
          <path d="M17 12c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-2.08c3.39-.49 6-3.39 6-6.92h-2z"/>
          <circle cx="12" cy="12" r="10" fill="none" stroke="currentColor" stroke-width="1.5" opacity="0.4" class="vt-pulse-ring"/>
        </svg>`;

      case 'processing':
        return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" width="18" height="18" class="vt-spin">
          <circle cx="12" cy="12" r="10" stroke-dasharray="30 70" stroke-linecap="round"/>
        </svg>`;

      case 'error':
        return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" width="18" height="18">
          <path d="M1 21h22L12 2 1 21zm12-3h-2v-2h2v2zm0-4h-2v-4h2v4z"/>
        </svg>`;

      default: // idle
        return `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="currentColor" width="18" height="18">
          <path d="M12 15c1.66 0 3-1.34 3-3V6c0-1.66-1.34-3-3-3S9 4.34 9 6v6c0 1.66 1.34 3 3 3zm-1-9c0-.55.45-1 1-1s1 .45 1 1v6c0 .55-.45 1-1 1s-1-.45-1-1V6z"/>
          <path d="M17 12c0 2.76-2.24 5-5 5s-5-2.24-5-5H5c0 3.53 2.61 6.43 6 6.92V21h2v-2.08c3.39-.49 6-3.39 6-6.92h-2z"/>
        </svg>`;
    }
  }

  // ─── Text Injection ───────────────────────────────────────────────────────────

  /**
   * Inject text into an input element in a framework-compatible way.
   * Handles React/Vue controlled inputs, contenteditable, and plain inputs.
   * @param {Element} element
   * @param {string}  text
   */
  function injectText(element, text) {
    if (!element || !text) return;

    if (element.isContentEditable) {
      // contenteditable (ChatGPT, Claude, Notion, etc.)
      element.focus();
      const selection = window.getSelection();
      if (selection && selection.rangeCount > 0) {
        const range = selection.getRangeAt(0);
        range.deleteContents();
        range.insertNode(document.createTextNode(text));
        range.collapse(false);
        selection.removeAllRanges();
        selection.addRange(range);
      } else {
        // Fallback: append text
        document.execCommand('insertText', false, text);
      }
      element.dispatchEvent(new Event('input',  { bubbles: true }));
      element.dispatchEvent(new Event('change', { bubbles: true }));
    } else {
      // textarea / input — use native setter to trigger React synthetic events
      const proto  = element.tagName === 'TEXTAREA'
        ? window.HTMLTextAreaElement.prototype
        : window.HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;

      if (setter) {
        // Append to existing value
        const current = element.value;
        setter.call(element, current ? `${current} ${text}` : text);
      } else {
        element.value = element.value ? `${element.value} ${text}` : text;
      }

      element.dispatchEvent(new Event('input',  { bubbles: true }));
      element.dispatchEvent(new Event('change', { bubbles: true }));
    }
  }

  // Listen for scroll/resize to keep button in sync
  window.addEventListener('scroll', () => reposition(), { passive: true });
  window.addEventListener('resize', () => reposition(), { passive: true });

  return {
    attachTo,
    detach,
    destroy,
    reposition,
    setState,
    injectText,
    setOnClick(cb)   { _onClick = cb; },
    get state()      { return _state; },
    get element()    { return _btn; },
  };
})();

window.__vtMicButton = micButton;
