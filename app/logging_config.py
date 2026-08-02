"""Structured logging with credential redaction applied at the handler.

Two independent guarantees:

1. No application log statement records user messages, model outputs, prompts,
   or secrets. That is a property of the call sites, verified by
   ``tests/test_observability.py``.
2. Nothing that reaches a log line carries a credential even if it arrives from
   a third-party traceback nobody wrote — a psycopg connection error quoting
   the DSN, an httpx error quoting a signed URL. That is this module's job.

The second exists because the first cannot cover code we do not own.
"""

import json
import logging
import re
import sys
from typing import Any


_REDACTIONS: tuple[tuple[re.Pattern[str], str], ...] = (
    # A database DSN carrying inline credentials: keep the shape, drop the
    # password.  # pragma: allowlist-secret
    (re.compile(r"(?i)\b(postgres(?:ql)?://[^:/\s]+:)[^@\s]+(@)"), r"\1***\2"),
    # Provider key formats: Anthropic, Voyage, Cohere, Supabase publishable keys.
    (re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{8,}"), "sk-ant-***"),
    (re.compile(r"\bpa-[A-Za-z0-9\-_]{16,}"), "pa-***"),
    (re.compile(r"\bsb(?:p|_secret|_publishable)?_[A-Za-z0-9\-_]{16,}"), "sb_***"),
    # Any JWT, which covers Supabase anon and service-role keys and user tokens.
    (
        re.compile(r"\beyJ[A-Za-z0-9\-_]{8,}\.[A-Za-z0-9\-_]{8,}\.[A-Za-z0-9\-_]+"),
        "<redacted-jwt>",
    ),
    # A bare `Bearer <token>` anywhere, with or without a header name in front.
    (re.compile(r"(?i)\bBearer\s+[A-Za-z0-9\-._~+/=]+"), "Bearer ***"),
    # Header name followed by its value, up to a quote, comma, or end of line.
    # The value pattern must not stop at the first space: a rendered header is
    # commonly `Authorization: "Bearer <token>"`, and stopping early would mask
    # the scheme and leave the credential itself in the log.
    (
        re.compile(
            r"(?i)\b(authorization|apikey|api[-_]?key|x-api-key|password|secret|token)"
            r"(['\"]?\s*[:=]\s*['\"]?)[^'\"\n,}]+"
        ),
        r"\1\2***",
    ),
)


def redact(text: str) -> str:
    """Mask credential-shaped substrings while leaving the message readable."""
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFilter(logging.Filter):
    """Scrub credentials from the rendered message and any exception text."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:  # pragma: no cover - a broken format string
            rendered = str(record.msg)
        record.msg = redact(rendered)
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    """One JSON object per line, which is what Railway's log drain expects."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            # Tracebacks belong in logs and never in an HTTP response body.
            payload["exception"] = redact(self.formatException(record.exc_info))
        if record.stack_info:
            payload["stack"] = redact(self.formatStack(record.stack_info))
        return json.dumps(payload, default=str)


def configure_logging(*, level: str, log_format: str) -> None:
    """Install the root handler. Safe to call more than once."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(RedactingFilter())
    handler.setFormatter(
        JsonFormatter()
        if log_format == "json"
        else logging.Formatter("%(asctime)s %(levelname)-8s %(name)s: %(message)s")
    )
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Access logs repeat what the platform already records and add request
    # paths containing cat and moment identifiers. Keep the error channel.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
