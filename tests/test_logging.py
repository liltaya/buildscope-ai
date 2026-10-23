from __future__ import annotations

import json
import logging

from buildscope.logging import JsonFormatter, configure_logging


def test_json_formatter_creates_valid_json() -> None:
    formatter = JsonFormatter()

    record = logging.LogRecord(
        name="buildscope.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=10,
        msg="test message",
        args=(),
        exc_info=None,
    )

    formatted = formatter.format(record)

    payload = json.loads(formatted)

    assert payload["level"] == "INFO"
    assert payload["logger"] == "buildscope.test"
    assert payload["message"] == "test message"
    assert "timestamp" in payload


def test_json_formatter_uses_utc_timestamp() -> None:
    formatter = JsonFormatter()

    record = logging.LogRecord(
        name="buildscope.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=20,
        msg="UTC test",
        args=(),
        exc_info=None,
    )

    formatted = formatter.format(record)

    payload = json.loads(formatted)

    timestamp = payload["timestamp"]

    assert timestamp.endswith("+00:00")


def test_configure_logging_returns_buildscope_logger() -> None:
    logger = configure_logging()

    assert logger.name == "buildscope"
    assert logger.level == logging.INFO
    assert logger.propagate is False
    assert len(logger.handlers) == 1


def test_configure_logging_respects_requested_level() -> None:
    logger = configure_logging(logging.DEBUG)

    assert logger.level == logging.DEBUG
def test_json_formatter_includes_exception() -> None:
    formatter = JsonFormatter()

    try:
        raise ValueError("example failure")
    except ValueError:
        import sys

        exception_info = sys.exc_info()

    record = logging.LogRecord(
        name="buildscope.test",
        level=logging.ERROR,
        pathname=__file__,
        lineno=50,
        msg="operation failed",
        args=(),
        exc_info=exception_info,
    )

    formatted = formatter.format(record)

    payload = json.loads(formatted)

    assert payload["level"] == "ERROR"
    assert payload["message"] == "operation failed"
    assert "exception" in payload
    assert "ValueError: example failure" in payload["exception"]
