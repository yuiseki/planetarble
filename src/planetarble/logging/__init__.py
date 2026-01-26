"""Logging utilities for Planetarble pipeline monitoring."""

from __future__ import annotations

import json
import logging
from logging import Logger
from logging.config import dictConfig
from typing import Optional


def log_step(
    logger: Logger,
    *,
    phase: str,
    step: str,
    command: Optional[list[str]] = None,
    extra: Optional[dict] = None,
) -> None:
    payload = {"phase": phase, "step": step}
    if command:
        payload["command"] = " ".join(command)
    if extra:
        payload.update(extra)
    logger.info("%s step: %s", phase, step, extra=payload)


def log_skip(
    logger: Logger,
    *,
    phase: str,
    reason: str,
    path: Optional[str] = None,
    extra: Optional[dict] = None,
) -> None:
    payload = {"phase": phase, "reason": reason}
    if path:
        payload["path"] = path
    if extra:
        payload.update(extra)
    logger.info("%s skip: %s", phase, reason, extra=payload)


def log_progress(
    logger: Logger,
    *,
    phase: str,
    step: str,
    current: int,
    total: Optional[int] = None,
    percent: Optional[float] = None,
    elapsed: Optional[str] = None,
    eta: Optional[str] = None,
    extra: Optional[dict] = None,
) -> None:
    payload = {
        "phase": phase,
        "step": step,
        "current": current,
        "total": total,
        "percent": percent,
        "elapsed": elapsed,
        "eta": eta,
    }
    if extra:
        payload.update(extra)
    if total:
        logger.info(
            "%s progress: %d/%d (%.1f%%)%s%s",
            phase,
            current,
            total,
            percent or 0.0,
            f" elapsed={elapsed}" if elapsed else "",
            f" eta={eta}" if eta else "",
            extra=payload,
        )
    else:
        logger.info(
            "%s progress: %d%s%s",
            phase,
            current,
            f" elapsed={elapsed}" if elapsed else "",
            f" eta={eta}" if eta else "",
            extra=payload,
        )


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
