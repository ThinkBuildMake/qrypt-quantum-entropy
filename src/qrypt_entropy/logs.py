"""Structured JSON logs. Never log values, bytes, keys or credentials (OB-2, OB-3, CP-1)."""

import base64
import json
import logging
import sys
from datetime import UTC, datetime
from pathlib import Path

import structlog

REDACTED = "[redacted]"
SENSITIVE_WORDS = frozenset(
    {"value", "key", "pepper", "token", "bytes", "secret", "password", "credential", "authorization", "random"}
)
MIN_MARKER_LENGTH = 8  # shorter secrets would corrupt ordinary words; field names cover them
_markers: list[str] = []  # longest first, so a marker that contains another is replaced whole


def register_secret(secret: str | bytes) -> None:
    """Make a secret or issued value unloggable: it is replaced anywhere in a written line, whether it appears
    raw, hex (either case), base64 (padded, unpadded or URL-safe) or JSON-escaped (OB-3, CP-1)."""
    raw = secret.encode() if isinstance(secret, str) else secret
    if not raw:
        return
    forms = {raw.decode(errors="ignore"), raw.hex(), raw.hex().upper()}
    for encode in (base64.b64encode, base64.urlsafe_b64encode):
        encoded = encode(raw).decode()
        forms |= {encoded, encoded.rstrip("=")}
    forms |= {json.dumps(form)[1:-1] for form in forms}
    _markers[:] = sorted({*_markers, *(f for f in forms if len(f) >= MIN_MARKER_LENGTH)}, key=len, reverse=True)


def _is_sensitive(name: str) -> bool:
    return not SENSITIVE_WORDS.isdisjoint(name.lower().replace("-", "_").split("_"))


def _redact(name: str, field):
    """Redact by field name (a count under a sensitive name is harmless) and bytes wherever they are."""
    if isinstance(field, bytes | bytearray):
        return REDACTED
    if isinstance(field, dict):
        return {k: _redact(str(k), v) for k, v in field.items()}
    if isinstance(field, list | tuple):
        return [_redact(name, v) for v in field]
    if _is_sensitive(name) and field is not None and not isinstance(field, int | float):
        return REDACTED
    return field


def _scrub_fields(_logger, _method, event_dict: dict) -> dict:
    return {name: _redact(name, field) for name, field in event_dict.items()}


def scrub_line(line: str) -> str:
    """The last defence: whatever was rendered, no registered marker survives in it."""
    for marker in _markers:
        line = line.replace(marker, REDACTED)
    return line


def _scrub_rendered(_logger, _method, line: str) -> str:
    return scrub_line(line)


class _LibraryJsonFormatter(logging.Formatter):
    """Records from libraries (asyncio, uvicorn, httpx) become JSON lines too; our own are already JSON."""

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if message.startswith('{"'):
            return scrub_line(message)
        fields = {"event": message, "level": record.levelname.lower(), "logger": record.name}
        fields["timestamp"] = datetime.fromtimestamp(record.created, UTC).isoformat().replace("+00:00", "Z")
        return scrub_line(json.dumps(fields))


def configure_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """One JSON object per line on stderr (stdout carries the MCP protocol) and, if given, a file."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    for handler in handlers:
        handler.setFormatter(_LibraryJsonFormatter())
    logging.basicConfig(level=level, handlers=handlers, force=True)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _scrub_fields,
            structlog.processors.JSONRenderer(default=repr),
            _scrub_rendered,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def get_logger(name: str = "qrypt_entropy"):
    return structlog.get_logger(name)


configure_logging()  # safe default on import: JSON to stderr, so a stray log call can never hit the MCP stdout
