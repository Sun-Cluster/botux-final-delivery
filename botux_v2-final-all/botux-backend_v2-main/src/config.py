from __future__ import annotations

import os
from dataclasses import dataclass


def _get_str(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def _get_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError:
        return default


def _get_float(name: str, default: float) -> float:
    raw = os.getenv(name, str(default)).strip()
    try:
        return float(raw)
    except ValueError:
        return default


def _get_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name, "1" if default else "0").strip().lower()
    return raw in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AppConfig:
    env: str
    api_host: str
    api_port: int
    log_level: str
    db_uri: str
    db_echo: bool
    db_auto_schema: bool
    skip_registry_bootstrap: bool
    io_worker_concurrency: int
    cpu_worker_concurrency: int
    openai_api_key_present: bool
    llm_provider: str
    llm_model: str
    llm_fast_model: str
    llm_daily_cap_usd: float


def load_configs() -> AppConfig:
    db_uri = _get_str("BOTUX_DB_URI", "")
    if not db_uri:
        raise RuntimeError("Missing required env var: BOTUX_DB_URI")
    skip_registry_bootstrap = _get_bool("BOTUX_SKIP_REGISTRY_BOOTSTRAP", False)
    if not skip_registry_bootstrap:
        skip_registry_bootstrap = _get_bool("BOTUX_SKIP_DB_INIT", False)
    return AppConfig(
        env=_get_str("BOTUX_ENV", "dev"),
        api_host=_get_str("BOTUX_API_HOST", "0.0.0.0"),
        api_port=_get_int("BOTUX_API_PORT", 8000),
        log_level=_get_str("BOTUX_LOG_LEVEL", "INFO"),
        db_uri=db_uri,
        db_echo=_get_bool("BOTUX_DB_ECHO", False),
        db_auto_schema=_get_bool("BOTUX_DB_AUTO_SCHEMA", False),
        skip_registry_bootstrap=skip_registry_bootstrap,
        io_worker_concurrency=_get_int("BOTUX_IO_WORKER_CONCURRENCY", 4),
        cpu_worker_concurrency=_get_int("BOTUX_CPU_WORKER_CONCURRENCY", 2),
        openai_api_key_present=bool(_get_str("OPENAI_API_KEY", "")),
        llm_provider=_get_str("BOTUX_LLM_PROVIDER", "openai"),
        llm_model=_get_str("BOTUX_LLM_MODEL", "gpt-4o"),
        llm_fast_model=_get_str("BOTUX_LLM_FAST_MODEL", "gpt-4o-mini"),
        llm_daily_cap_usd=_get_float("BOTUX_LLM_DAILY_CAP_USD", 5.0),
    )
