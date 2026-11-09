"""Tests for the logging setup.

These check the plain promises in the module: logging is set up once,
levels are checked, and each line comes out as readable JSON.
"""

import json
import logging

import pytest

from sre_agent.logging_config import JsonFormatter, configure_logging, get_logger


def test_configure_returns_main_logger_with_level():
    logger = configure_logging("DEBUG")
    assert logger.name == "sre_agent"
    assert logger.level == logging.DEBUG


def test_configure_is_safe_to_call_twice():
    configure_logging("INFO")
    configure_logging("WARNING")
    logger = logging.getLogger("sre_agent")
    marked = [h for h in logger.handlers if getattr(h, "_sre_agent_handler", False)]
    assert len(marked) == 1
    assert logger.level == logging.WARNING


def test_configure_rejects_unknown_level():
    with pytest.raises(ValueError):
        configure_logging("chatty")


def test_get_logger_stays_in_the_family():
    assert get_logger().name == "sre_agent"
    assert get_logger("policy").name == "sre_agent.policy"
    assert get_logger("sre_agent.models").name == "sre_agent.models"


def test_log_line_is_json_with_the_key_fields():
    record = logging.LogRecord(
        name="sre_agent.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="incident %s opened",
        args=("abc",),
        exc_info=None,
    )
    entry = json.loads(JsonFormatter().format(record))
    assert entry["level"] == "INFO"
    assert entry["logger"] == "sre_agent.test"
    assert entry["message"] == "incident abc opened"
    assert "time" in entry
