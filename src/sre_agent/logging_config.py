"""Logging setup for sre-agent.

The agent must be easy to watch while it works. So every log line is
written as one JSON object on one line. A person can still read it,
and a log tool can search and filter it without guessing.

Use it like this:

    from sre_agent.logging_config import configure_logging, get_logger

    configure_logging("INFO")
    log = get_logger(__name__)
    log.info("incident opened")

Only this module sets up handlers. Other modules just call
``get_logger`` so we never print the same line twice.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime

_ROOT_NAME = "sre_agent"
_HANDLER_MARK = "_sre_agent_handler"


class JsonFormatter(logging.Formatter):
    """Format one log record as one line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, object] = {
            "time": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info and record.exc_info[0] is not None:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO") -> logging.Logger:
    """Set up logging once and return the main ``sre_agent`` logger.

    Calling this again is safe: it changes the level and does not
    add a second handler, so lines are never printed twice.
    A level name that Python does not know raises ``ValueError``.
    """
    level_name = level.upper()
    if level_name not in logging.getLevelNamesMapping():
        raise ValueError(f"unknown log level: {level!r}")

    logger = logging.getLogger(_ROOT_NAME)
    logger.setLevel(level_name)
    # Keep our lines from also flowing through the root logger twice.
    logger.propagate = False

    for handler in logger.handlers:
        if getattr(handler, _HANDLER_MARK, False):
            handler.setLevel(level_name)
            return logger

    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    setattr(handler, _HANDLER_MARK, True)
    logger.addHandler(handler)
    return logger


def get_logger(name: str | None = None) -> logging.Logger:
    """Return a logger inside the ``sre_agent`` family.

    Pass ``__name__`` from the calling module. Short names are also
    fine: ``get_logger("policy")`` returns ``sre_agent.policy``.
    """
    if not name or name == _ROOT_NAME:
        return logging.getLogger(_ROOT_NAME)
    if name.startswith(f"{_ROOT_NAME}."):
        return logging.getLogger(name)
    return logging.getLogger(f"{_ROOT_NAME}.{name}")
