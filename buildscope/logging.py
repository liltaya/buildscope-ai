"""Structured logging utilities for BuildScope AI."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any


class JsonFormatter(logging.Formatter):
    """Format log records as JSON objects."""

    def format(self, record: logging.LogRecord) -> str:
        """Convert a LogRecord into a JSON string."""
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        if record.exc_info is not None:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        )


def configure_logging(
    level: int = logging.INFO,
) -> logging.Logger:
    """Configure and return the BuildScope logger."""
    logger = logging.getLogger("buildscope")

    logger.setLevel(level)
    logger.propagate = False

    logger.handlers.clear()

    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())

    logger.addHandler(handler)

    return logger
