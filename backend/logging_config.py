"""VocalTwist structured logging configuration.

Provides:
* ``setup_logging``     — configure the root logger once at startup.
* ``get_logger``        — return a module-level logger with PII masking attached.
* ``mask_pii``          — redact emails, phone numbers, and SSNs from strings.
* ``PIIMaskingFilter``  — ``logging.Filter`` subclass that applies PII masking.
* ``JSONFormatter``     — ``logging.Formatter`` that emits newline-delimited JSON.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import traceback
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# PII patterns
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)
_PHONE_RE = re.compile(
    r"(?<!\d)(\+?1[\s\-.]?)?\(?\d{3}\)?[\s\-.]?\d{3}[\s\-.]?\d{4}(?!\d)",
)
_SSN_RE = re.compile(
    r"(?<!\d)\d{3}[\s\-]\d{2}[\s\-]\d{4}(?!\d)",
)
_AADHAAR_RE = re.compile(
    r"(?<!\d)\d{4}[\s\-]?\d{4}[\s\-]?\d{4}(?!\d)",
)


def mask_pii(text: str) -> str:
    """Redact personally-identifiable information from *text*.

    Replaces:
    * Email addresses → ``[EMAIL]``
    * Phone numbers   → ``[PHONE]``
    * SSNs (US)       → ``[SSN]``
    * Aadhaar numbers → ``[AADHAAR]``

    Args:
        text: Any string (log message, exception text, etc.).

    Returns:
        The same string with PII tokens replaced by safe placeholders.
    """
    text = _EMAIL_RE.sub("[EMAIL]", text)
    text = _PHONE_RE.sub("[PHONE]", text)
    text = _SSN_RE.sub("[SSN]", text)
    text = _AADHAAR_RE.sub("[AADHAAR]", text)
    return text


# ---------------------------------------------------------------------------
# PII masking filter
# ---------------------------------------------------------------------------


class PIIMaskingFilter(logging.Filter):
    """``logging.Filter`` that runs :func:`mask_pii` on every log record.

    Attach to any handler or logger::

        logger.addFilter(PIIMaskingFilter())

    The filter mutates ``record.msg`` and ``record.args`` in-place so the
    formatted output is always PII-free regardless of the formatter used.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            record.msg = mask_pii(str(record.msg))
            if record.args:
                if isinstance(record.args, dict):
                    record.args = {k: mask_pii(str(v)) for k, v in record.args.items()}
                elif isinstance(record.args, tuple):
                    record.args = tuple(mask_pii(str(a)) for a in record.args)
        except Exception:  # noqa: BLE001
            pass  # Never let the filter break the logging pipeline.
        return True


# ---------------------------------------------------------------------------
# JSON log formatter
# ---------------------------------------------------------------------------

_RESERVED_LOG_ATTRS = frozenset(
    (
        "args", "asctime", "created", "exc_info", "exc_text", "filename",
        "funcName", "levelname", "levelno", "lineno", "message", "module",
        "msecs", "msg", "name", "pathname", "process", "processName",
        "relativeCreated", "stack_info", "thread", "threadName",
    )
)


class JSONFormatter(logging.Formatter):
    """Emit each log record as a single-line JSON object.

    Standard fields emitted:

    * ``timestamp`` — ISO-8601 UTC
    * ``level``     — log level name
    * ``logger``    — logger name
    * ``message``   — formatted message
    * ``module``    — source module
    * ``lineno``    — source line number
    * ``exc``       — exception traceback (only if an exception is attached)

    Any extra key/value pairs passed via ``extra={}`` are merged at the top
    level of the JSON object.
    """

    def format(self, record: logging.LogRecord) -> str:
        record.message = record.getMessage()
        data: dict = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.message,
            "module": record.module,
            "lineno": record.lineno,
        }

        # Merge any extra fields (e.g. request_id, duration_ms)
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_ATTRS and not key.startswith("_"):
                data[key] = value

        if record.exc_info:
            data["exc"] = self.formatException(record.exc_info)
        elif record.exc_text:
            data["exc"] = record.exc_text

        try:
            return json.dumps(data, default=str)
        except Exception:  # noqa: BLE001
            return json.dumps({"level": "ERROR", "message": "Log serialisation failed"})


# ---------------------------------------------------------------------------
# Plain-text formatter (for human-readable local development)
# ---------------------------------------------------------------------------

class TextFormatter(logging.Formatter):
    """Coloured, human-readable formatter for development environments."""

    _COLOURS = {
        "DEBUG":    "\033[36m",   # Cyan
        "INFO":     "\033[32m",   # Green
        "WARNING":  "\033[33m",   # Yellow
        "ERROR":    "\033[31m",   # Red
        "CRITICAL": "\033[35m",   # Magenta
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        colour = self._COLOURS.get(record.levelname, "")
        prefix = f"{colour}[{record.levelname}]{self._RESET}"
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime("%H:%M:%S")
        msg = record.getMessage()
        base = f"{ts} {prefix} {record.name}: {msg}"
        if record.exc_info:
            base += "\n" + self.formatException(record.exc_info)
        return base


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def setup_logging(level: str = "INFO", fmt: str = "json") -> None:
    """Configure the root logger for VocalTwist.

    Call this once, early in application startup (e.g. in ``create_app()``).

    Args:
        level: Logging level string (e.g. ``"INFO"``, ``"DEBUG"``).
        fmt:   ``"json"`` for production structured logging,
               ``"text"`` for human-readable development output.
    """
    numeric_level = getattr(logging, level.upper(), logging.INFO)

    formatter: logging.Formatter
    if fmt.lower() == "json":
        formatter = JSONFormatter()
    else:
        formatter = TextFormatter()

    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(numeric_level)
    handler.setFormatter(formatter)
    handler.addFilter(PIIMaskingFilter())

    root = logging.getLogger()
    root.setLevel(numeric_level)

    # Remove any pre-existing handlers to avoid duplicate output when called
    # inside test suites or frameworks that install their own handlers.
    root.handlers.clear()
    root.addHandler(handler)

    # Quieten noisy third-party loggers.
    logging.getLogger("faster_whisper").setLevel(logging.WARNING)
    logging.getLogger("edge_tts").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    """Return a named logger with the PII masking filter pre-attached.

    Args:
        name: Logger name — use ``__name__`` from the calling module.

    Returns:
        A :class:`logging.Logger` instance with :class:`PIIMaskingFilter`
        attached so all records emitted through it are PII-safe.
    """
    log = logging.getLogger(name)
    # Attach the filter only once even if called multiple times.
    if not any(isinstance(f, PIIMaskingFilter) for f in log.filters):
        log.addFilter(PIIMaskingFilter())
    return log
