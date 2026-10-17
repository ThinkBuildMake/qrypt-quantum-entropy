"""Structured JSON logs. Never log values, bytes, keys or credentials (OB-2, OB-3, CP-1)."""

import logging
import sys
from pathlib import Path

import structlog


def configure_logging(level: str = "INFO", log_file: str | None = None) -> None:
    """One JSON object per line on stderr (stdout carries the MCP protocol) and, if given, a file."""
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file))
    logging.basicConfig(level=level, handlers=handlers, format="%(message)s", force=True)
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer(),
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=False,
    )


def get_logger(name: str = "qrypt_entropy"):
    return structlog.get_logger(name)


configure_logging()  # safe default on import: JSON to stderr, so a stray log call can never hit the MCP stdout
