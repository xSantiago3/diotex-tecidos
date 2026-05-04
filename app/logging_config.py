from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from typing import Any


_STANDARD_RECORD_FIELDS = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys())


class CloudJsonFormatter(logging.Formatter):
    """Structured JSON formatter tuned for Google Cloud Logging."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "severity": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        for key, value in record.__dict__.items():
            if key in _STANDARD_RECORD_FIELDS or key.startswith("_"):
                continue
            payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)

        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging() -> None:
    """Configure root logging once with a JSON formatter for Cloud Logging."""
    root = logging.getLogger()
    if getattr(root, "_diotex_logging_configured", False):
        return

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(CloudJsonFormatter())

    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    logging.captureWarnings(True)

    root._diotex_logging_configured = True
