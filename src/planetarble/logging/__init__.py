"""Logging utilities for Planetarble pipeline monitoring."""

from __future__ import annotations

import json
import logging
from logging import Logger
from logging.config import dictConfig
from typing import Optional


class JSONFormatter(logging.Formatter):
    """Structured JSON formatter for pipeline logs."""

    def format(self, record: logging.LogRecord) -> str:  # pragma: no cover - thin wrapper
        payload = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def configure_logging(
    *,
    level: str = "INFO",
    json_logs: bool = False,
    log_file: Optional[str] = None,
) -> None:
    """Configure global logging handlers and formatters."""

    formatters = {
        "standard": {
            "format": "%(asctime)s | %(levelname)s | %(name)s | %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%SZ",
        }
    }
    if json_logs:
        formatters["json"] = {
            "()": JSONFormatter,
            "datefmt": "%Y-%m-%dT%H:%M:%SZ",
        }

    handlers = {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json" if json_logs else "standard",
        }
    }

    if log_file:
        handlers["file"] = {
            "class": "logging.FileHandler",
            "filename": log_file,
            "encoding": "utf-8",
            "formatter": "json" if json_logs else "standard",
        }

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": formatters,
            "handlers": handlers,
            "root": {
                "handlers": list(handlers.keys()),
                "level": level.upper(),
            },
        }
    )


def get_logger(name: str) -> Logger:
    """Return a module-scoped logger."""

    return logging.getLogger(name)
