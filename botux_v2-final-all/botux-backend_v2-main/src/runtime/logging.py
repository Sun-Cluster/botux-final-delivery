from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from enum import Enum
from pathlib import Path
from threading import Lock

import loguru
from loguru import logger

_VALID_LEVELS = {"TRACE", "DEBUG", "INFO", "SUCCESS", "WARNING", "ERROR", "CRITICAL"}


class _SchedulerJobFileSink:
    def __init__(self, log_dir: Path) -> None:
        self._dir = log_dir / "scheduler"
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def __call__(self, message: loguru.Message) -> None:
        record = message.record
        extra = record.get("extra")
        if not isinstance(extra, dict):
            return
        job_name = extra.get("scheduler_job")
        if not isinstance(job_name, str) or not job_name.strip():
            return
        safe_name = _slug(job_name)
        path = self._dir / f"{safe_name}.log"
        text = str(message)
        with self._lock:
            with path.open("a", encoding="utf-8") as handle:
                handle.write(text)


def _slug(value: str) -> str:
    normalized = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value.strip().lower())
    return normalized or "unknown_job"


def _is_pipeline_record(record: loguru.Record) -> bool:
    message = record.get("message")
    return isinstance(message, str) and message.startswith("pipeline.")


def _is_scheduler_job_record(record: loguru.Record) -> bool:
    extra = record.get("extra")
    return isinstance(extra, dict) and isinstance(extra.get("scheduler_job"), str)


def _is_app_record(record: loguru.Record) -> bool:
    return not _is_pipeline_record(record) and not _is_scheduler_job_record(record)


def _normalize_level(level: str | None) -> str:
    candidate = (level or "").strip().upper()
    if candidate in _VALID_LEVELS:
        return candidate
    return "INFO"


def configure_logging(level: str | None = None) -> str:
    resolved_level = _normalize_level(level or os.getenv("BOTUX_LOG_LEVEL", "INFO"))
    log_dir = Path(os.getenv("BOTUX_LOG_DIR", "logs"))
    log_dir.mkdir(parents=True, exist_ok=True)
    scheduler_sink = _SchedulerJobFileSink(log_dir)
    logger.remove()
    logger.add(
        sys.stdout,
        level=resolved_level,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> "
            "<level>[{level}]</level> "
            "<cyan>[{module}:{function}:{line}]</cyan>: "
            "<level>{message}</level>"
        ),
        colorize=True,
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        str(log_dir / "pipeline.log"),
        level=resolved_level,
        filter=_is_pipeline_record,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} "
            "[{level}] "
            "[{module}:{function}:{line}]: "
            "{message}"
        ),
        rotation="10 MB",
        retention=5,
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        scheduler_sink,
        level=resolved_level,
        filter=_is_scheduler_job_record,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} "
            "[{level}] "
            "[{module}:{function}:{line}]: "
            "{message}"
        ),
        backtrace=False,
        diagnose=False,
    )
    logger.add(
        str(log_dir / "app.log"),
        level=resolved_level,
        filter=_is_app_record,
        format=(
            "{time:YYYY-MM-DD HH:mm:ss.SSS} "
            "[{level}] "
            "[{module}:{function}:{line}]: "
            "{message}"
        ),
        rotation="10 MB",
        retention=5,
        backtrace=False,
        diagnose=False,
    )
    return resolved_level


def format_log_fields(fields: dict[str, object]) -> str:
    return _format_fields(fields)


def _format_fields(fields: dict[str, object]) -> str:
    parts: list[str] = []
    for key in sorted(fields.keys()):
        value = fields[key]
        if value is None:
            continue
        parts.append(f"{key}={_serialize(value)}")
    return " ".join(parts)


def _serialize(value: object) -> str:
    if isinstance(value, Enum):
        return _serialize(value.value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, (list, dict, tuple, set)):
        return json.dumps(value, default=str, sort_keys=True, separators=(",", ":"))
    return json.dumps(str(value))
