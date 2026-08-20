from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.services.runtime_config.bootstrap import bootstrap_runtime_controls
from db.repositories._common import JSONValue
from db.repositories.bots_repo import BotsRepository
from db.uow import UnitOfWork


CANONICAL_BOT_PROFILES: dict[str, dict[str, JSONValue]] = {
    "turbo": {
        "display_name": "Vetra",
        "mission": "High-conviction intraday momentum execution on US equities.",
        "strategy_type": "intraday_momentum",
        "horizon": "intraday",
        "market": "us_equities",
        "broker": "alpaca",
        "mode": "paper",
        "lifecycle_state": "paper",
        "status": "ready",
        "intel_source": "newsfeed_intel",
        "enabled": True,
        "profile_eligible": True,
        "performance_owner": True,
        "allocation": {"pct": 0.28, "max_positions": 4},
        "risk": {"per_trade_pct": 0.01, "daily_loss_pct": 0.025, "max_notional": 30000.0},
        "order_types": ["market", "bracket"],
        "allowed_brokers": ["alpaca"],
        "compat_aliases": ["turbo", "vetra"],
    },
    "drifter": {
        "display_name": "Axon",
        "mission": "Multi-day swing entries with controlled momentum filters.",
        "strategy_type": "swing_momentum",
        "horizon": "swing",
        "market": "us_equities",
        "broker": "alpaca",
        "mode": "paper",
        "lifecycle_state": "paper",
        "status": "ready",
        "autopilot_state": "active",
        "intel_source": "scout_engine",
        "enabled": True,
        "profile_eligible": True,
        "performance_owner": True,
        "allocation": {"pct": 0.32, "max_positions": 6},
        "risk": {"per_trade_pct": 0.0125, "daily_loss_pct": 0.03, "max_notional": 40000.0},
        "execution_policy": {
            "capital_basis": "buying_power",
            "min_buying_power_usd": 100.0,
            "skip_scan_when_insufficient_buying_power": True,
        },
        "order_types": ["market", "bracket"],
        "allowed_brokers": ["alpaca"],
        "compat_aliases": ["swingtrade", "drifter"],
    },
    "gambler": {
        "display_name": "Prism",
        "mission": "Event-driven options deployment with strict gating.",
        "strategy_type": "options_premium",
        "horizon": "intraday",
        "market": "options_us",
        "broker": "alpaca",
        "mode": "paper",
        "lifecycle_state": "paper",
        "status": "ready",
        "autopilot_state": "active",
        "intel_source": "options_lane",
        "enabled": True,
        "profile_eligible": True,
        "performance_owner": True,
        "allocation": {"pct": 0.12, "max_positions": 3},
        "risk": {"per_trade_pct": 0.008, "daily_loss_pct": 0.02, "max_notional": 15000.0},
        "order_types": ["limit"],
        "allowed_brokers": ["alpaca"],
        "compat_aliases": ["options", "gambler"],
    },
    "copycat": {
        "display_name": "Echo",
        "mission": "Institutional flow replication with delayed confirmation.",
        "strategy_type": "institutional_replication",
        "horizon": "swing",
        "market": "us_equities",
        "broker": "alpaca",
        "mode": "paper",
        "lifecycle_state": "paper",
        "status": "ready",
        "autopilot_state": "active",
        "intel_source": "tradecopy_lane",
        "enabled": True,
        "profile_eligible": True,
        "performance_owner": True,
        "allocation": {"pct": 0.08, "max_positions": 3},
        "risk": {"per_trade_pct": 0.0075, "daily_loss_pct": 0.015, "max_notional": 10000.0},
        "order_types": ["limit"],
        "allowed_brokers": ["alpaca"],
        "compat_aliases": ["tradecopy", "copycat"],
    },
    "nugget_bot": {
        "display_name": "Forge",
        "mission": "ASX mining catalyst and permit intelligence execution.",
        "strategy_type": "ausmine_event",
        "horizon": "swing",
        "market": "asx_equities",
        "broker": "ibkr",
        "mode": "paper",
        "lifecycle_state": "paper",
        "status": "ready",
        "autopilot_state": "active",
        "intel_source": "ausmine_lane",
        "enabled": True,
        "profile_eligible": True,
        "performance_owner": True,
        "allocation": {"pct": 0.2, "max_positions": 5},
        "risk": {"per_trade_pct": 0.01, "daily_loss_pct": 0.02, "max_notional": 25000.0},
        "order_types": ["limit", "market"],
        "allowed_brokers": ["ibkr"],
        "compat_aliases": ["ausmining", "nugget", "miner"],
    },
    "evo_catalyst": {
        "display_name": "Volt",
        "mission": "Energy-transition catalyst execution across ASX and thematic proxies.",
        "strategy_type": "evo_catalyst_event",
        "horizon": "swing",
        "market": "asx_equities",
        "broker": "ibkr",
        "mode": "paper",
        "lifecycle_state": "paper",
        "status": "ready",
        "autopilot_state": "active",
        "intel_source": "evo_catalyst_lane",
        "enabled": True,
        "profile_eligible": True,
        "performance_owner": True,
        "allocation": {"pct": 0.1, "max_positions": 3},
        "risk": {"per_trade_pct": 0.008, "daily_loss_pct": 0.015, "max_notional": 12000.0},
        "order_types": ["limit", "market"],
        "allowed_brokers": ["ibkr"],
        "notes": "Live catalyst lane using news/article evidence plus market confirmation.",
        "compat_aliases": ["evo", "evo_catalyst", "volt"],
    },
    "signal_engine": {
        "display_name": "Signal Engine",
        "strategy_type": "technical_signal_scanning",
        "market": "us_equities",
        "broker": "none",
        "mode": "system",
        "lifecycle_state": "live",
        "status": "live",
        "enabled": True,
        "profile_eligible": False,
        "performance_owner": False,
    },
    "risk_engine": {
        "display_name": "Risk Engine",
        "strategy_type": "risk_enforcement",
        "market": "all",
        "broker": "none",
        "mode": "system",
        "lifecycle_state": "live",
        "status": "live",
        "enabled": True,
        "profile_eligible": False,
        "performance_owner": False,
    },
    "watchman": {
        "display_name": "Position Monitor",
        "strategy_type": "position_monitoring",
        "market": "all",
        "broker": "multi",
        "mode": "system",
        "lifecycle_state": "live",
        "status": "live",
        "enabled": True,
        "profile_eligible": False,
        "performance_owner": False,
    },
}

CANONICAL_STRATEGIES: dict[str, dict[str, JSONValue]] = {
    "strat_turbo_v1": {
        "name": "Turbo Momentum",
        "version": "v1",
        "lifecycle_state": "paper",
        "bot_ids": ["turbo"],
        "market": "us_equities",
        "broker": "alpaca",
        "signal_sources": ["watchlist_momentum", "scout"],
        "order_types": ["market", "bracket"],
        "min_score_threshold": 0.65,
        "min_confidence": 0.6,
    },
    "strat_drifter_v1": {
        "name": "Drifter Swing",
        "version": "v1",
        "lifecycle_state": "paper",
        "bot_ids": ["drifter"],
        "market": "us_equities",
        "broker": "alpaca",
        "signal_sources": ["swingtrade"],
        "order_types": ["market", "bracket"],
        "min_score_threshold": 0.62,
        "min_confidence": 0.58,
    },
    "strat_gambler_v1": {
        "name": "Options Premium",
        "version": "v1",
        "lifecycle_state": "paper",
        "bot_ids": ["gambler"],
        "market": "options_us",
        "broker": "alpaca",
        "signal_sources": ["options_flow", "options"],
        "order_types": ["limit"],
        "min_score_threshold": 0.68,
        "min_confidence": 0.62,
    },
    "strat_copycat_v1": {
        "name": "Institutional Replication",
        "version": "v1",
        "lifecycle_state": "paper",
        "bot_ids": ["copycat"],
        "market": "us_equities",
        "broker": "alpaca",
        "signal_sources": ["tradecopy", "13f"],
        "order_types": ["limit"],
        "min_score_threshold": 0.6,
        "min_confidence": 0.55,
    },
    "strat_ausmine_v1": {
        "name": "AusMine Event",
        "version": "v1",
        "lifecycle_state": "paper",
        "bot_ids": ["nugget_bot"],
        "market": "asx_equities",
        "broker": "ibkr",
        "signal_sources": ["ausmine", "nugget_permit"],
        "order_types": ["market", "limit"],
        "min_score_threshold": 0.64,
        "min_confidence": 0.58,
    },
    "strat_evo_catalyst_v1": {
        "name": "EVO Catalyst",
        "version": "v1",
        "lifecycle_state": "paper",
        "bot_ids": ["evo_catalyst"],
        "market": "asx_equities",
        "broker": "ibkr",
        "signal_sources": ["evo_catalyst"],
        "order_types": ["market", "limit"],
        "min_score_threshold": 0.66,
        "min_confidence": 0.6,
    },
}

_CORE_BOT_ALIAS_MAP: dict[str, str] = {
    "swingtrade": "drifter",
    "options": "gambler",
    "tradecopy": "copycat",
    "ausmining": "nugget_bot",
    "nugget": "nugget_bot",
}

_DEFAULT_ENABLED_TRADING_BOTS: frozenset[str] = frozenset(
    {"copycat", "drifter", "gambler", "nugget_bot", "evo_catalyst"}
)


async def seed_registry(
    *,
    mode: str = "repair",
    reference_profile_sources: list[Path] | None = None,
) -> dict[str, object]:
    normalized_mode = mode.strip().lower()
    if normalized_mode not in {"missing_only", "repair", "replace"}:
        raise ValueError(f"unsupported seed mode '{mode}'")

    runtime_controls = await bootstrap_runtime_controls()
    reference_profiles, reference_source = _load_reference_profiles(sources=reference_profile_sources)
    reference_profiles_written = 0

    async with UnitOfWork() as uow:
        repo = BotsRepository(connection=uow.connection)
        existing_profiles = await repo.list_bot_profiles()
        existing_strategies = await repo.list_strategy_registry()
        profiles_written = 0
        strategies_written = 0

        for bot_id, canonical_profile in CANONICAL_BOT_PROFILES.items():
            existing = existing_profiles.get(bot_id)
            merged = _merge(existing=existing, canonical=canonical_profile, mode=normalized_mode)
            merged = _apply_canonical_upgrade(bot_id=bot_id, profile=merged, mode=normalized_mode)
            if merged is None:
                continue
            await repo.upsert_bot_profile(bot_id, merged)
            existing_profiles[bot_id] = merged
            profiles_written += 1

        if normalized_mode != "replace":
            for source_bot_id, reference_profile in reference_profiles.items():
                normalized_bot_id = _normalize_profile_bot_id(source_bot_id)
                existing = existing_profiles.get(normalized_bot_id)
                merged = _merge(existing=existing, canonical=reference_profile, mode=normalized_mode)
                if merged is None:
                    continue
                await repo.upsert_bot_profile(normalized_bot_id, merged)
                existing_profiles[normalized_bot_id] = merged
                reference_profiles_written += 1

        for strategy_id, canonical_strategy in CANONICAL_STRATEGIES.items():
            existing = existing_strategies.get(strategy_id)
            merged = _merge(existing=existing, canonical=canonical_strategy, mode=normalized_mode)
            merged = _apply_strategy_upgrade(strategy_id=strategy_id, metadata=merged, mode=normalized_mode)
            if merged is None:
                continue
            await repo.upsert_strategy_registry(strategy_id, merged)
            strategies_written += 1

    return {
        "mode": normalized_mode,
        "runtime_controls": runtime_controls,
        "profiles_written": profiles_written,
        "reference_profiles_detected": len(reference_profiles),
        "reference_profiles_written": reference_profiles_written,
        "reference_source": str(reference_source) if reference_source is not None else None,
        "strategies_written": strategies_written,
        "profile_count": len(CANONICAL_BOT_PROFILES),
        "strategy_count": len(CANONICAL_STRATEGIES),
        "seeded_at": datetime.now(timezone.utc).isoformat(),
    }


def _merge(
    *,
    existing: dict[str, JSONValue] | None,
    canonical: dict[str, JSONValue],
    mode: str,
) -> dict[str, JSONValue] | None:
    if mode == "missing_only":
        if existing is not None:
            return None
        return canonical

    if mode == "replace":
        return canonical

    base = existing or {}
    merged = _deep_fill(base=base, defaults=canonical)
    if existing is not None and merged == existing:
        return None
    return merged


def _deep_fill(*, base: dict[str, JSONValue], defaults: dict[str, JSONValue]) -> dict[str, JSONValue]:
    result: dict[str, JSONValue] = dict(base)
    for key, default_value in defaults.items():
        if key not in result or _is_empty(result[key]):
            result[key] = default_value
            continue
        existing_value = result[key]
        if isinstance(existing_value, dict) and isinstance(default_value, dict):
            result[key] = _deep_fill(base=existing_value, defaults=default_value)
    return result


def _is_empty(value: JSONValue) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() == ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) == 0
    return False


def _normalize_profile_bot_id(bot_id: str) -> str:
    return _CORE_BOT_ALIAS_MAP.get(bot_id, bot_id)


def _apply_canonical_upgrade(
    *,
    bot_id: str,
    profile: dict[str, JSONValue] | None,
    mode: str,
) -> dict[str, JSONValue] | None:
    if profile is None or mode == "missing_only":
        return profile
    upgraded = dict(profile)
    if bot_id == "evo_catalyst":
        lifecycle = str(upgraded.get("lifecycle_state") or "").strip().lower()
        status = str(upgraded.get("status") or "").strip().lower()
        notes = str(upgraded.get("notes") or "").strip().lower()
        if lifecycle == "shadow" or status == "shadow" or "non-functional" in notes:
            upgraded["lifecycle_state"] = "paper"
            upgraded["status"] = "ready"
            upgraded["notes"] = "Live catalyst lane using news/article evidence plus market confirmation."
    if bot_id == "drifter":
        execution_policy = upgraded.get("execution_policy")
        if not isinstance(execution_policy, dict):
            execution_policy = {}
        normalized_policy = dict(execution_policy)
        capital_basis = str(normalized_policy.get("capital_basis") or "").strip().lower()
        if capital_basis not in {"buying_power", "equity"}:
            normalized_policy["capital_basis"] = "buying_power"
        min_buying_power = normalized_policy.get("min_buying_power_usd")
        if not isinstance(min_buying_power, (int, float)) or float(min_buying_power) < 0:
            normalized_policy["min_buying_power_usd"] = 100.0
        skip_scan = normalized_policy.get("skip_scan_when_insufficient_buying_power")
        if not isinstance(skip_scan, bool):
            normalized_policy["skip_scan_when_insufficient_buying_power"] = True
        upgraded["execution_policy"] = normalized_policy
    if bot_id in _DEFAULT_ENABLED_TRADING_BOTS:
        upgraded["enabled"] = True
        upgraded["autopilot_state"] = "active"
        lifecycle = str(upgraded.get("lifecycle_state") or "").strip().lower()
        if lifecycle in {"", "shadow", "testing", "standby"}:
            upgraded["lifecycle_state"] = "paper"
        status = str(upgraded.get("status") or "").strip().lower()
        if status in {"", "shadow", "standby", "inactive", "disabled"}:
            upgraded["status"] = "ready"
    return upgraded


def _apply_strategy_upgrade(
    *,
    strategy_id: str,
    metadata: dict[str, JSONValue] | None,
    mode: str,
) -> dict[str, JSONValue] | None:
    if metadata is None or mode == "missing_only" or strategy_id != "strat_evo_catalyst_v1":
        return metadata
    upgraded = dict(metadata)
    lifecycle = str(upgraded.get("lifecycle_state") or "").strip().lower()
    sources = upgraded.get("signal_sources")
    if lifecycle == "shadow":
        upgraded["lifecycle_state"] = "paper"
    if isinstance(sources, list) and any(str(item) in {"evo_intel", "evo_quality"} for item in sources):
        upgraded["signal_sources"] = ["evo_catalyst"]
    return upgraded


def _load_reference_profiles(*, sources: list[Path] | None) -> tuple[dict[str, dict[str, JSONValue]], Path | None]:
    candidates = list(sources or _default_reference_profile_sources())
    for candidate in candidates:
        raw = _read_json_dict(candidate)
        if not raw:
            continue
        profiles_raw = raw.get("profiles")
        if not isinstance(profiles_raw, dict):
            continue
        profiles: dict[str, dict[str, JSONValue]] = {}
        for bot_id, payload in profiles_raw.items():
            if not isinstance(bot_id, str) or not isinstance(payload, dict):
                continue
            mapped_id = _normalize_profile_bot_id(bot_id)
            normalized_payload = _as_json_dict(payload)
            if not normalized_payload:
                continue
            # Keep source identifier in profile metadata for audit/debug.
            normalized_payload.setdefault("reference_source_bot_id", bot_id)
            aliases = _string_list(normalized_payload.get("compat_aliases"))
            aliases.extend([bot_id, mapped_id])
            dedup_aliases = sorted({item for item in aliases if item})
            if dedup_aliases:
                normalized_payload["compat_aliases"] = dedup_aliases
            profiles[mapped_id] = normalized_payload
        if profiles:
            return profiles, candidate
    return {}, None


def _default_reference_profile_sources() -> list[Path]:
    project_root = Path(__file__).resolve().parents[4]
    sources: list[Path] = []
    snapshots_root = project_root / "docs" / "context" / "artifacts" / "snapshots"
    if snapshots_root.exists():
        snapshot_dirs = [item for item in snapshots_root.iterdir() if item.is_dir()]
        snapshot_dirs.sort(key=lambda item: item.name, reverse=True)
        for snapshot_dir in snapshot_dirs:
            sources.append(snapshot_dir / "data" / "bot_profiles.json")
    sources.append(project_root.parent / "botux-backend" / "data" / "bot_profiles.json")
    return sources


def _read_json_dict(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return raw


def _as_json_dict(payload: dict[object, object]) -> dict[str, JSONValue]:
    result: dict[str, JSONValue] = {}
    for key, value in payload.items():
        if not isinstance(key, str):
            continue
        if _is_json_compatible(value):
            result[key] = value
    return result


def _string_list(value: JSONValue | None) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if isinstance(item, str) and str(item).strip()]


def _is_json_compatible(value: object) -> bool:
    if value is None:
        return True
    if isinstance(value, (str, int, float, bool)):
        return True
    if isinstance(value, list):
        return all(_is_json_compatible(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_compatible(item) for key, item in value.items())
    return False
