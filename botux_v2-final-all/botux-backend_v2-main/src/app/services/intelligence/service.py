from __future__ import annotations

import asyncio
import json
import pickle
from datetime import date, datetime, timedelta, timezone
from importlib.util import find_spec
from math import sqrt
from pathlib import Path
from typing import TYPE_CHECKING, TypedDict

from app.services.intelligence.evaluators import (
    CORR_LOOKBACK_DAYS,
    CORR_MAX_OVERLAP,
    CORR_THRESHOLD,
    EARNINGS_BLOCK_DAYS,
    EARNINGS_CLOSE_DAYS,
    MAX_PER_SECTOR,
    PDT_MAX_DAY_TRADES,
    PDT_THRESHOLD_USD,
    earnings_action,
    evaluate_regime,
    evaluate_time_window,
    pearson_correlation,
    pdt_can_trade,
    realized_volatility,
    recent_day_trade_count,
    sector_concentration_state,
    sector_for_symbol,
)
from app.services.market.data import MarketDataService
from app.services.runtime_config.service import RuntimeConfigService
from db.models import CouncilDecisionRecord, TradeOutcomeRecord
from db.repositories._common import JSONValue
from db.repositories.audit_logs_repo import AuditLogsRepository
from db.repositories.signals_repo import SignalsRepository
from db.repositories.trade_outcomes_repo import TradeOutcomesRepository
from db.uow import UnitOfWork
from domain.models.trade_outcome import TradeOutcome

if TYPE_CHECKING:
    from runtime.container import Container

_ML_WATCHLIST: tuple[str, ...] = (
    "BHP.AX",
    "RIO.AX",
    "PLS.AX",
    "LTR.AX",
    "NCM.AX",
    "NST.AX",
    "IGO.AX",
    "SFR.AX",
    "GDX",
    "GDXJ",
    "COPX",
    "LIT",
    "AAPL",
    "NVDA",
)

_MEMORY_INTEL_ARTIFACTS: list[tuple[str, dict[str, JSONValue]]] = []
_ML_TRAIN_THRESHOLD = 50
_ML_RETRAIN_INTERVAL = 30
_ML_MODEL_CACHE: dict[str, object | None] = {
    "path": None,
    "mtime": None,
    "model": None,
    "error": None,
}
class _MlStageSpec(TypedDict):
    stage_id: int
    stage_name: str
    min_trades: int
    min_accuracy: float | None
    min_f1: float | None
    vote_weight: float
    roles_active: list[str]


class _PerformanceSummary(TypedDict):
    total_trades: int
    wins: int
    losses: int
    win_rate: float
    avg_pnl_pct: float
    total_pnl: float
    pnl_values: list[float]
    pnl_volatility: float


class _MlArtifactState(TypedDict):
    model_dir: str
    model_path: str
    metrics_path: str
    feature_names_path: str
    model_exists: bool
    metrics_exists: bool
    feature_names_exists: bool
    artifacts_complete: bool
    feature_count: int
    feature_names: list[object]
    metrics: dict[str, JSONValue]
    last_modified: str | None
    load_error: str | None
    loaded_model_type: str | None


class _MlRuntimeStatus(TypedDict):
    status: str
    accuracy: float | None
    f1_score: float | None
    model_loaded: bool
    model_version: str | None
    model_path: str
    last_train: str | None
    next_train_at: int
    score_mode: str
    dependencies: dict[str, object]
    artifact_state: _MlArtifactState
    feature_names: list[object]
    features_collected: int
    graduation: dict[str, object]
    message: str


class _BarRow(TypedDict):
    close: float
    volume: int
    timestamp: str


_ML_STAGE_SPECS: tuple[_MlStageSpec, ...] = (
    {
        "stage_id": 0,
        "stage_name": "Observer",
        "min_trades": 0,
        "min_accuracy": None,
        "min_f1": None,
        "vote_weight": 0.5,
        "roles_active": [],
    },
    {
        "stage_id": 1,
        "stage_name": "Learner",
        "min_trades": 50,
        "min_accuracy": None,
        "min_f1": None,
        "vote_weight": 1.0,
        "roles_active": ["regime_assist", "signal_ranking", "source_scoring", "drift_detection"],
    },
    {
        "stage_id": 2,
        "stage_name": "Advisor",
        "min_trades": 200,
        "min_accuracy": 60.0,
        "min_f1": 0.55,
        "vote_weight": 1.25,
        "roles_active": [
            "regime_assist",
            "signal_ranking",
            "source_scoring",
            "drift_detection",
            "bot_expectancy_forecasting",
        ],
    },
    {
        "stage_id": 3,
        "stage_name": "Trusted",
        "min_trades": 500,
        "min_accuracy": 65.0,
        "min_f1": 0.60,
        "vote_weight": 1.5,
        "roles_active": [
            "regime_assist",
            "signal_ranking",
            "source_scoring",
            "drift_detection",
            "bot_expectancy_forecasting",
            "pretrade_warning",
        ],
    },
    {
        "stage_id": 4,
        "stage_name": "Senior",
        "min_trades": 1000,
        "min_accuracy": 70.0,
        "min_f1": 0.65,
        "vote_weight": 2.0,
        "roles_active": [
            "regime_assist",
            "signal_ranking",
            "source_scoring",
            "drift_detection",
            "bot_expectancy_forecasting",
            "pretrade_warning",
            "allocation_influence",
        ],
    },
)


class IntelligenceService:
    async def regime_snapshot(self, container: Container) -> dict[str, object]:
        perf = await self._performance_summary()
        spy_quote, spy_bars, vix_bars = await asyncio.gather(
            self._safe_quote(container, "SPY"),
            self._market_bars("SPY", range_name="1y"),
            self._market_bars("^VIX", range_name="6mo"),
        )
        spy_price = _float_value(spy_quote.get("last"), 0.0)
        if spy_bars:
            closes = [row["close"] for row in spy_bars]
            spy_price = closes[-1]
            spy_ma200 = sum(closes[-200:]) / min(200, len(closes))
        else:
            spy_ma200 = spy_price if spy_price > 0.0 else 100.0
            if spy_price <= 0.0:
                spy_price = 100.0
        if vix_bars:
            vix = float(vix_bars[-1]["close"])
        else:
            pnl_values = perf["pnl_values"]
            pnl_volatility = _stddev(pnl_values)
            vix = round(18.0 + (pnl_volatility * 14.0) + ((1.0 - perf["win_rate"]) * 8.0), 2)
        held_symbols = await self._held_symbols(container)
        sector_state = sector_concentration_state(held_symbols)
        watchlist_near_earnings = await self._watchlist_near_earnings()
        evaluation = evaluate_regime(
            vix=vix,
            spy_price=spy_price,
            spy_ma200=spy_ma200,
            trading_halted=container.trading_halted,
            event_heavy=watchlist_near_earnings >= 3,
            sector_concentration=sector_state,
        )
        spy_above_ma = spy_price >= spy_ma200 if spy_ma200 > 0.0 else True
        return {
            "regime": evaluation.primary_regime,
            "primary_regime": evaluation.primary_regime,
            "multiplier": evaluation.multiplier,
            "vix": round(vix, 2),
            "spy_price": round(spy_price, 4),
            "spy_ma200": round(spy_ma200, 4),
            "spy_above_ma": spy_above_ma,
            "last_update": _iso_now(),
            "should_trade": evaluation.should_trade,
            "trend": evaluation.trend,
            "sub_regime": evaluation.sub_regime,
            "event_density": evaluation.event_density,
            "sector_concentration": evaluation.sector_concentration,
            "bot_eligibility": evaluation.bot_eligibility,
            "source": "market_regime_engine",
            "generated_at": _iso_now(),
        }

    async def regime_status(self, container: Container) -> dict[str, object]:
        snapshot = await self.regime_snapshot(container)
        regime = str(snapshot["regime"])
        multiplier = _float_value(snapshot.get("multiplier"), 0.0)
        return {
            "regime": regime,
            "score": self._regime_score(regime),
            "sizing_mult": multiplier,
            "multiplier": multiplier,
            "generated_at": _iso_now(),
        }

    async def correlation_status(self) -> dict[str, object]:
        cached_symbols = await self._correlation_universe_symbols(limit=12)
        return {
            "enabled": True,
            "window": f"{CORR_LOOKBACK_DAYS}d",
            "threshold": CORR_THRESHOLD,
            "max_overlap": CORR_MAX_OVERLAP,
            "max_per_sector": MAX_PER_SECTOR,
            "method": "market_data_correlation",
            "cached_symbols": len(cached_symbols),
            "generated_at": _iso_now(),
        }

    async def correlation_matrix(self, container: Container) -> dict[str, object]:
        held_symbols = await self._held_symbols(container)
        symbols = held_symbols[:6] if held_symbols else ["AAPL", "NVDA", "MSFT"]
        matrix_rows: list[dict[str, object]] = []
        for symbol in symbols:
            row: dict[str, object] = {"symbol": symbol}
            for other in symbols:
                row[other] = 1.0 if symbol == other else round(await self._pair_correlation(symbol, other), 3)
            matrix_rows.append(row)
        return {"symbols": symbols, "matrix": matrix_rows, "generated_at": _iso_now()}

    async def correlation_check(self, symbol: str, container: Container) -> dict[str, object]:
        normalized = symbol.upper()
        held_symbols = await self._held_symbols(container)
        if not held_symbols:
            return {
                "symbol": normalized,
                "allow_entry": True,
                "allowed": True,
                "max_corr_observed": 0.0,
                "threshold": CORR_THRESHOLD,
                "held_symbols": [],
                "correlated_with": [],
                "generated_at": _iso_now(),
            }
        candidate_sector = sector_for_symbol(normalized)
        sector_count = sum(1 for held in held_symbols if sector_for_symbol(held) == candidate_sector)
        if sector_count >= MAX_PER_SECTOR:
            return {
                "symbol": normalized,
                "allow_entry": False,
                "allowed": False,
                "max_corr_observed": 0.0,
                "threshold": CORR_THRESHOLD,
                "held_symbols": held_symbols,
                "correlated_with": [],
                "reason": f"Sector limit: {sector_count}/{MAX_PER_SECTOR} in {candidate_sector}",
                "generated_at": _iso_now(),
            }
        correlated_with: list[dict[str, object]] = []
        for held in held_symbols:
            if held == normalized:
                continue
            corr = await self._pair_correlation(normalized, held)
            if corr >= CORR_THRESHOLD:
                correlated_with.append({"symbol": held, "correlation": round(corr, 3)})
        max_corr = max((_float_value(item.get("correlation"), 0.0) for item in correlated_with), default=0.0)
        allow_entry = len(correlated_with) < CORR_MAX_OVERLAP
        return {
            "symbol": normalized,
            "allow_entry": allow_entry,
            "allowed": allow_entry,
            "max_corr_observed": round(max_corr, 4),
            "threshold": CORR_THRESHOLD,
            "held_symbols": held_symbols,
            "correlated_with": correlated_with,
            "reason": "Clear" if allow_entry else f"High correlation with {len(correlated_with)} positions",
            "generated_at": _iso_now(),
        }

    async def filters_status(self) -> dict[str, object]:
        time_window = evaluate_time_window()
        return {
            "enabled": True,
            "current_zone": time_window.zone,
            "trading_allowed": time_window.allowed,
            "reason": time_window.reason,
            "filters": {
                "time_of_day": True,
                "liquidity": True,
                "spread": True,
                "volatility": True,
                "volume_profile": True,
                "event_risk": True,
            },
            "config": {
                "lookback_days": CORR_LOOKBACK_DAYS,
                "min_volume_ratio": 1.5,
                "earnings_block_days": EARNINGS_BLOCK_DAYS,
                "earnings_close_days": EARNINGS_CLOSE_DAYS,
            },
            "generated_at": _iso_now(),
        }

    async def filters_check(self, symbol: str, container: Container) -> dict[str, object]:
        normalized = symbol.upper()
        quote = await self._safe_quote(container, normalized)
        bid = _float_value(quote.get("bid"), 0.0)
        ask = _float_value(quote.get("ask"), 0.0)
        last = _float_value(quote.get("last"), 0.0)
        if last <= 0.0:
            last = ask or bid or 100.0
        spread_pct = 0.0 if last <= 0.0 else (ask - bid) / last
        bars = await self._market_bars(normalized, range_name="6mo")
        closes = [row["close"] for row in bars]
        volumes = [int(row["volume"]) for row in bars]
        volume_ratio = self._volume_ratio(volumes)
        volatility = realized_volatility(closes[-21:])
        volatility_score = min(1.0, max(0.0, volatility / 0.05)) if volatility > 0.0 else 0.0
        earnings = await self.earnings_check(normalized)
        time_window = evaluate_time_window()

        failures: list[str] = []
        if not time_window.allowed:
            failures.append("time_of_day")
        if last < 5.0:
            failures.append("liquidity")
        if spread_pct > 0.01:
            failures.append("spread")
        if volatility_score > 0.9:
            failures.append("volatility")
        if volume_ratio > 0.0 and volume_ratio < 1.5:
            failures.append("volume_profile")
        if bool(earnings.get("near_earnings", False)):
            failures.append("event_risk")

        allowed = len(failures) == 0
        return {
            "symbol": normalized,
            "pass": allowed,
            "allowed": allowed,
            "failed_filters": failures,
            "reason": "all_clear" if allowed else ",".join(failures),
            "metrics": {
                "last": round(last, 4),
                "spread_pct": round(spread_pct, 6),
                "volatility_score": round(volatility_score, 4),
                "volume_ratio": round(volume_ratio, 4),
                "time_zone": time_window.zone,
            },
            "time": {"allowed": time_window.allowed, "zone": time_window.zone, "reason": time_window.reason},
            "volume": {"allowed": volume_ratio == 0.0 or volume_ratio >= 1.5, "volume_ratio": round(volume_ratio, 4)},
            "earnings": earnings,
            "generated_at": _iso_now(),
        }

    async def pdt_status(self, container: Container) -> dict[str, object]:
        account = await self._safe_account(container)
        equity = _float_value(account.get("equity"), 0.0)
        used = await self._recent_day_trade_count()
        can_trade, remaining, reason = pdt_can_trade(equity=equity, day_trades_used=used)
        unlimited = equity >= PDT_THRESHOLD_USD
        return {
            "enabled": True,
            "status": "unrestricted" if unlimited else "restricted" if not can_trade else "active",
            "equity": round(equity, 2),
            "day_trades_used": used,
            "day_trades_limit": 999 if unlimited else PDT_MAX_DAY_TRADES,
            "day_trades_remaining": remaining,
            "can_trade": can_trade,
            "pdt_applies": not unlimited,
            "reason": reason,
            "generated_at": _iso_now(),
        }

    async def earnings_check(self, symbol: str) -> dict[str, object]:
        normalized = symbol.upper()
        earnings_date = await MarketDataService().fetch_earnings_date(normalized)
        days_until = None
        if earnings_date:
            try:
                days_until = (datetime.strptime(earnings_date, "%Y-%m-%d").date() - datetime.now(timezone.utc).date()).days
            except ValueError:
                days_until = None
        payload = earnings_action(days_until)
        payload.update(
            {
                "symbol": normalized,
                "date": earnings_date or "",
                "sources": ["nasdaq_api"],
                "generated_at": _iso_now(),
            }
        )
        return payload

    async def permits_status(self) -> dict[str, object]:
        latest = await self._latest_artifact("lane.scan.ausmine")
        candidates: list[dict[str, object]] = []
        source_events: list[dict[str, object]] = []
        if latest is not None:
            raw_candidates = latest.get("candidates")
            if isinstance(raw_candidates, list):
                candidates = [_object_dict(item) for item in raw_candidates if isinstance(item, dict)]
            raw_sources = latest.get("source_events")
            if isinstance(raw_sources, list):
                source_events = [_object_dict(item) for item in raw_sources if isinstance(item, dict)]
        alerts = [
            {
                "symbol": str(item.get("symbol", "")),
                "tier": str(item.get("tier", "")),
                "confidence": item.get("confidence"),
            }
            for item in candidates[:3]
        ]
        last_scan = None if latest is None else latest.get("scan_at")
        sources = self._permit_sources(source_events)
        return {
            "status": "active",
            "last_scan": last_scan,
            "permits_tracked": len(candidates),
            "alerts": alerts,
            "message": "Permits scanner active — monitors ASX mining permits",
            "sources": sources,
            "recent_sources": source_events[:10],
            "generated_at": _iso_now(),
        }

    async def ml_status(self) -> dict[str, object]:
        perf = await self._performance_summary()
        evaluations = await self._ml_evaluations(limit=200)
        runtime = await self._ml_runtime_status(total_trades=perf["total_trades"], evaluations=evaluations)
        latest_evaluation = evaluations[0] if evaluations else None
        latest_eval_time = _string_value((latest_evaluation or {}).get("generated_at")) if latest_evaluation else None
        recent_eval_count = len(evaluations)
        return {
            "status": runtime["status"],
            "sample_size": perf["total_trades"],
            "total_trades_logged": perf["total_trades"],
            "features_collected": runtime["features_collected"],
            "model_version": runtime["model_version"],
            "model_path": runtime["model_path"],
            "last_train": runtime["last_train"],
            "last_evaluate": latest_eval_time,
            "recent_evaluations": recent_eval_count,
            "accuracy": runtime["accuracy"],
            "f1_score": runtime["f1_score"],
            "win_rate": round(perf["win_rate"], 4),
            "next_train_at": runtime["next_train_at"],
            "model_loaded": runtime["model_loaded"],
            "message": runtime["message"],
            "score_mode": runtime["score_mode"],
            "dependencies": runtime["dependencies"],
            "artifact_state": runtime["artifact_state"],
            "graduation": runtime["graduation"],
            "generated_at": _iso_now(),
        }

    async def ml_scores(self, container: Container, *, limit: int = 20) -> dict[str, object]:
        safe_limit = max(1, min(limit, len(_ML_WATCHLIST)))
        perf = await self._performance_summary()
        evaluations = await self._ml_evaluations(limit=50)
        runtime = await self._ml_runtime_status(total_trades=perf["total_trades"], evaluations=evaluations)
        perf_by_symbol = await self._performance_by_symbol()
        regime = await self.regime_snapshot(container)
        rows: list[dict[str, object]] = []
        for symbol in _ML_WATCHLIST[:safe_limit]:
            symbol_perf = perf_by_symbol.get(symbol.upper(), {"win_rate": 0.5, "avg_pnl_pct": 0.0, "trades": 0})
            feature_map = await self._ml_feature_map(
                symbol.upper(),
                container,
                regime=regime,
                symbol_perf=symbol_perf,
                feature_names=runtime["feature_names"] if isinstance(runtime["feature_names"], list) else [],
            )
            score_payload = await self._score_symbol_with_runtime(
                symbol.upper(),
                feature_map=feature_map,
                runtime=runtime,
                symbol_perf=symbol_perf,
            )
            quote = await self._safe_quote(container, symbol)
            rows.append(
                {
                    "symbol": symbol.upper(),
                    "ml_score": score_payload["score"],
                    "confidence": score_payload["confidence"],
                    "label": score_payload["label"],
                    "last": round(_float_value(quote.get("last"), 100.0), 4),
                    "source": score_payload["source"],
                    "score_mode": score_payload["score_mode"],
                    "model_loaded": runtime["model_loaded"],
                    "model_version": runtime["model_version"],
                    "created_at": _iso_now(),
                }
            )
        return {
            "scores": rows,
            "count": len(rows),
            "scored_at": _iso_now(),
            "score_mode": runtime["score_mode"],
            "model_loaded": runtime["model_loaded"],
            "model_version": runtime["model_version"],
        }

    async def ml_evaluate(self, symbol: str, container: Container) -> dict[str, object]:
        normalized = symbol.upper()
        perf = await self._performance_summary()
        evaluations = await self._ml_evaluations(limit=50)
        runtime = await self._ml_runtime_status(total_trades=perf["total_trades"], evaluations=evaluations)
        perf_by_symbol = await self._performance_by_symbol()
        symbol_perf = perf_by_symbol.get(normalized, {"win_rate": 0.5, "avg_pnl_pct": 0.0, "trades": 0})
        regime = await self.regime_snapshot(container)
        feature_map = await self._ml_feature_map(
            normalized,
            container,
            regime=regime,
            symbol_perf=symbol_perf,
            feature_names=runtime["feature_names"] if isinstance(runtime["feature_names"], list) else [],
        )
        score_payload = await self._score_symbol_with_runtime(
            normalized,
            feature_map=feature_map,
            runtime=runtime,
            symbol_perf=symbol_perf,
        )
        payload = {
            "symbol": normalized,
            "score": score_payload["score"],
            "confidence": score_payload["confidence"],
            "label": score_payload["label"],
            "regime": str(regime["regime"]),
            "trades": symbol_perf["trades"],
            "score_mode": score_payload["score_mode"],
            "model_loaded": runtime["model_loaded"],
            "model_version": runtime["model_version"],
            "dependencies": runtime["dependencies"],
            "artifact_state": {
                "artifacts_complete": runtime["artifact_state"]["artifacts_complete"],
                "feature_count": runtime["artifact_state"]["feature_count"],
                "load_error": runtime["artifact_state"]["load_error"],
            },
            "features": feature_map,
            "generated_at": _iso_now(),
        }
        await self._append_artifact("ml.evaluate", _json_payload(payload))
        return payload

    async def llm_status(self, container: Container | None = None) -> dict[str, object]:
        provider = _string_value(getattr(getattr(container, "config", None), "llm_provider", None)) or "openai"
        model = _string_value(getattr(getattr(container, "config", None), "llm_model", None)) or "gpt-4o"
        model_fast = _string_value(getattr(getattr(container, "config", None), "llm_fast_model", None)) or "gpt-4o-mini"
        daily_cap = _float_value(getattr(getattr(container, "config", None), "llm_daily_cap_usd", None), 5.0)
        api_key_present = bool(getattr(getattr(container, "config", None), "openai_api_key_present", False))
        activity = await self._llm_activity_summary()
        status = "disabled"
        message = "OPENAI_API_KEY not configured."
        if api_key_present and _as_int(activity.get("call_count")) > 0:
            status = "active"
            message = f"LLM runtime active with {activity['call_count']} tracked calls today."
        elif api_key_present:
            status = "ready"
            message = "LLM runtime configured; no tracked analyses yet."
        return {
            "status": status,
            "enabled": api_key_present,
            "provider": provider,
            "model": model,
            "model_fast": model_fast,
            "models": [model, model_fast],
            "daily_cost": activity["daily_cost"],
            "daily_cap": round(daily_cap, 4),
            "call_count": activity["call_count"],
            "cache_size": activity["cache_size"],
            "recent_analyses": activity["recent_analyses"],
            "last_analysis_at": activity["last_analysis_at"],
            "tracking_source": "audit_logs",
            "message": message,
            "generated_at": _iso_now(),
        }

    async def _ml_runtime_status(
        self,
        *,
        total_trades: int,
        evaluations: list[dict[str, JSONValue]],
    ) -> _MlRuntimeStatus:
        dependencies = self._ml_dependency_state()
        artifact_state = await self._ml_artifact_state()
        metrics = artifact_state["metrics"]
        feature_names = artifact_state["feature_names"]
        model_runtime = await self._ml_model_runtime(artifact_state)
        accuracy = _normalize_percentage(metrics.get("accuracy"))
        f1_score = _normalize_ratio(metrics.get("f1_score"))
        graduation = self._ml_graduation_state(total_trades=total_trades, accuracy=accuracy, f1_score=f1_score)
        if model_runtime["loaded"]:
            status = "trained"
        elif total_trades >= _ML_TRAIN_THRESHOLD and bool(dependencies["training_ready"]):
            status = "ready"
        elif total_trades >= _ML_TRAIN_THRESHOLD:
            status = "blocked"
        else:
            status = "collecting"
        missing_dependencies = [
            name
            for name in ("numpy", "xgboost")
            if not bool(dependencies.get(name, False))
        ]
        load_error = _string_value(model_runtime.get("load_error"))
        if status == "trained":
            message = f"Model artifact loaded from {artifact_state['model_path']}."
        elif status == "ready":
            message = f"{total_trades} trades collected. Training prerequisites satisfied; model artifacts not present yet."
        elif status == "blocked" and missing_dependencies:
            message = f"{total_trades} trades collected, but ML runtime is blocked by missing dependencies: {', '.join(missing_dependencies)}."
        elif status == "blocked" and load_error:
            message = f"Model artifacts detected but could not be loaded: {load_error}."
        else:
            message = f"Collecting trade data. {total_trades}/{_ML_TRAIN_THRESHOLD} trades toward first training."
        if total_trades < _ML_TRAIN_THRESHOLD:
            next_train_at = _ML_TRAIN_THRESHOLD
        elif model_runtime["loaded"]:
            next_train_at = total_trades + _ML_RETRAIN_INTERVAL
        else:
            next_train_at = total_trades
        model_version = None
        model_version = _string_value(metrics.get("feature_set_version")) or _string_value(metrics.get("model_version"))
        if not model_version and bool(model_runtime["loaded"]):
            model_version = Path(str(artifact_state["model_path"])).name
        artifact_state["load_error"] = _string_value(model_runtime.get("load_error"))
        artifact_state["loaded_model_type"] = _string_value(model_runtime.get("model_type"))
        return {
            "status": status,
            "accuracy": accuracy,
            "f1_score": f1_score,
            "model_loaded": bool(model_runtime["loaded"]),
            "model_version": model_version,
            "model_path": artifact_state["model_path"],
            "last_train": _string_value(metrics.get("trained_at")),
            "next_train_at": next_train_at,
            "score_mode": "model_artifact" if bool(model_runtime["loaded"]) else "heuristic_fallback",
            "dependencies": dependencies,
            "artifact_state": artifact_state,
            "feature_names": feature_names,
            "features_collected": len(feature_names) if isinstance(feature_names, list) and feature_names else 12,
            "graduation": graduation,
            "message": message,
        }

    def _ml_dependency_state(self) -> dict[str, object]:
        numpy_ready = find_spec("numpy") is not None
        xgboost_ready = find_spec("xgboost") is not None
        return {
            "numpy": numpy_ready,
            "xgboost": xgboost_ready,
            "training_ready": numpy_ready and xgboost_ready,
            "artifact_inference_ready": numpy_ready,
        }

    async def _ml_artifact_state(self) -> _MlArtifactState:
        model_dir = self._ml_model_dir()
        model_path = model_dir / "signal_scorer.pkl"
        metrics_path = model_dir / "metrics.json"
        feature_names_path = model_dir / "feature_names.json"
        metrics = await asyncio.to_thread(_read_json_file, metrics_path)
        feature_names = await asyncio.to_thread(_read_json_file, feature_names_path)
        existing_paths = [path for path in (model_path, metrics_path, feature_names_path) if path.exists()]
        last_modified = None
        if existing_paths:
            last_modified = datetime.fromtimestamp(
                max(path.stat().st_mtime for path in existing_paths),
                tz=timezone.utc,
            ).isoformat()
        safe_feature_names: list[object] = list(feature_names) if isinstance(feature_names, list) else []
        return {
            "model_dir": str(model_dir),
            "model_path": str(model_path),
            "metrics_path": str(metrics_path),
            "feature_names_path": str(feature_names_path),
            "model_exists": model_path.exists(),
            "metrics_exists": metrics_path.exists(),
            "feature_names_exists": feature_names_path.exists(),
            "artifacts_complete": model_path.exists() and metrics_path.exists() and feature_names_path.exists(),
            "feature_count": len(safe_feature_names),
            "feature_names": safe_feature_names,
            "metrics": metrics if isinstance(metrics, dict) else {},
            "last_modified": last_modified,
            "load_error": None,
            "loaded_model_type": None,
        }

    async def _ml_model_runtime(self, artifact_state: _MlArtifactState) -> dict[str, object]:
        model_path_raw = artifact_state["model_path"]
        if not artifact_state["model_exists"]:
            return {"loaded": False, "load_error": None, "model_type": None}
        model_path = Path(model_path_raw)
        try:
            mtime = model_path.stat().st_mtime
        except OSError as exc:
            return {"loaded": False, "load_error": str(exc), "model_type": None}
        if _ML_MODEL_CACHE["path"] == str(model_path) and _ML_MODEL_CACHE["mtime"] == mtime:
            model = _ML_MODEL_CACHE.get("model")
            return {
                "loaded": model is not None,
                "load_error": _string_value(_ML_MODEL_CACHE.get("error")),
                "model_type": type(model).__name__ if model is not None else None,
            }
        try:
            model = await asyncio.to_thread(_load_pickled_model, model_path)
        except Exception as exc:  # pragma: no cover - exercised through payload behavior
            _ML_MODEL_CACHE.update({"path": str(model_path), "mtime": mtime, "model": None, "error": str(exc)})
            return {"loaded": False, "load_error": str(exc), "model_type": None}
        _ML_MODEL_CACHE.update({"path": str(model_path), "mtime": mtime, "model": model, "error": None})
        return {"loaded": True, "load_error": None, "model_type": type(model).__name__}

    def _ml_model_dir(self) -> Path:
        return Path(__file__).resolve().parents[3] / "models"

    def _ml_graduation_state(
        self,
        *,
        total_trades: int,
        accuracy: float | None,
        f1_score: float | None,
    ) -> dict[str, object]:
        current = _ML_STAGE_SPECS[0]
        for spec in _ML_STAGE_SPECS:
            if total_trades < spec["min_trades"]:
                continue
            min_accuracy = spec["min_accuracy"]
            min_f1 = spec["min_f1"]
            if min_accuracy is not None and (accuracy is None or accuracy < min_accuracy):
                continue
            if min_f1 is not None and (f1_score is None or f1_score < min_f1):
                continue
            current = spec
        next_stage = None
        for spec in _ML_STAGE_SPECS:
            if spec["stage_id"] <= current["stage_id"]:
                continue
            next_stage = {
                "stage_id": spec["stage_id"],
                "stage_name": spec["stage_name"],
                "min_trades": spec["min_trades"],
                "min_accuracy": spec["min_accuracy"],
                "min_f1": spec["min_f1"],
            }
            break
        return {
            "stage_id": current["stage_id"],
            "stage_name": current["stage_name"],
            "vote_weight": current["vote_weight"],
            "roles_active": list(current["roles_active"]),
            "next_stage": next_stage,
        }

    async def _ml_feature_map(
        self,
        symbol: str,
        container: Container,
        *,
        regime: dict[str, object],
        symbol_perf: dict[str, float | int],
        feature_names: list[object],
    ) -> dict[str, float | int]:
        include_history = any(
            str(name) in {"tech_score", "rsi", "momentum_5d", "volume_ratio", "ema_signal_num"}
            for name in feature_names
        )
        closes: list[float] = []
        volumes: list[int] = []
        if include_history:
            bars = await self._market_bars(symbol, range_name="6mo")
            closes = [float(row["close"]) for row in bars]
            volumes = [int(row["volume"]) for row in bars]
        quote = await self._safe_quote(container, symbol)
        last = _float_value(quote.get("last"), closes[-1] if closes else 100.0)
        avg_pnl_pct = float(symbol_perf.get("avg_pnl_pct", 0.0))
        win_rate = float(symbol_perf.get("win_rate", 0.5))
        trades = int(symbol_perf.get("trades", 0))
        multiplier = _float_value(regime.get("multiplier"), 0.65)
        momentum_5d = _pct_move(closes[-6], closes[-1]) if len(closes) >= 6 else 0.0
        volume_ratio = self._volume_ratio(volumes) if volumes else 0.0
        short_avg = sum(closes[-10:]) / 10 if len(closes) >= 10 else last
        long_avg = sum(closes[-20:]) / 20 if len(closes) >= 20 else short_avg
        ema_signal_num = 1 if short_avg > long_avg else -1 if short_avg < long_avg else 0
        rsi = _approx_rsi(closes[-15:]) if len(closes) >= 15 else 50.0
        tech_score = max(0.0, min(100.0, 50.0 + (momentum_5d * 2.5) + ((rsi - 50.0) * 0.35)))
        combined_score = max(
            0.0,
            min(
                100.0,
                42.0 + (win_rate * 28.0) + (multiplier * 18.0) + (min(max(avg_pnl_pct, -4.0), 4.0) * 3.0),
            ),
        )
        confidence = max(0.2, min(0.98, 0.42 + (trades * 0.012) + (win_rate * 0.18)))
        now_utc = datetime.now(timezone.utc)
        return {
            "combined_score": round(combined_score, 4),
            "sentiment": round(max(-1.0, min(1.0, (avg_pnl_pct / 5.0) + ((multiplier - 0.65) * 0.8))), 4),
            "confidence": round(confidence, 4),
            "tech_score": round(tech_score, 4),
            "rsi": round(rsi, 4),
            "momentum_5d": round(momentum_5d, 4),
            "volume_ratio": round(volume_ratio, 4),
            "ema_signal_num": ema_signal_num,
            "is_buy": 1,
            "bot_turbo": 0,
            "bot_ausmining": 1 if symbol.endswith(".AX") else 0,
            "bot_options": 0,
            "bot_swingtrade": 0 if symbol.endswith(".AX") else 1,
            "vix_at_entry": round(_float_value(regime.get("vix"), 18.0), 4),
            "sl_pct_used": 0.03,
            "hour_of_day": now_utc.hour,
            "day_of_week": now_utc.weekday(),
        }

    async def _score_symbol_with_runtime(
        self,
        symbol: str,
        *,
        feature_map: dict[str, float | int],
        runtime: _MlRuntimeStatus,
        symbol_perf: dict[str, float | int],
    ) -> dict[str, object]:
        if bool(runtime["model_loaded"]):
            score = await self._predict_model_score(
                feature_map=feature_map,
                feature_names=[str(name) for name in runtime["feature_names"]] if isinstance(runtime["feature_names"], list) else [],
            )
            if score is not None:
                confidence = max(0.25, min(0.99, 0.5 + abs(score - 0.5)))
                return {
                    "score": round(score, 4),
                    "confidence": round(confidence, 4),
                    "label": _ml_label(score),
                    "score_mode": "model_artifact",
                    "source": "ml_model_artifact",
                }
        win_rate = float(symbol_perf.get("win_rate", 0.5))
        avg_pnl_pct = float(symbol_perf.get("avg_pnl_pct", 0.0))
        trades = int(symbol_perf.get("trades", 0))
        combined_score = _float_value(feature_map.get("combined_score"), 50.0) / 100.0
        tech_score = _float_value(feature_map.get("tech_score"), 50.0) / 100.0
        confidence_signal = _float_value(feature_map.get("confidence"), 0.5)
        raw_score = 0.2 + (combined_score * 0.32) + (tech_score * 0.18) + (win_rate * 0.2) + min(max(avg_pnl_pct, -4.0), 4.0) * 0.03
        score = max(0.0, min(1.0, raw_score))
        confidence = max(0.35, min(0.97, confidence_signal + (trades * 0.008)))
        return {
            "score": round(score, 4),
            "confidence": round(confidence, 4),
            "label": _ml_label(score),
            "score_mode": "heuristic_fallback",
            "source": "ml_heuristic_fallback",
        }

    async def _predict_model_score(self, *, feature_map: dict[str, float | int], feature_names: list[str]) -> float | None:
        model = _ML_MODEL_CACHE.get("model")
        if model is None:
            return None
        safe_feature_names = feature_names or [key for key in feature_map.keys()]
        vector = [float(feature_map.get(name, 0.0)) for name in safe_feature_names]
        return await asyncio.to_thread(_predict_probability, model, vector)

    async def _llm_activity_summary(self) -> dict[str, object]:
        rows = await self._artifact_rows(prefix="llm.", limit=200)
        today = datetime.now(timezone.utc).date()
        daily_cost = 0.0
        recent_analyses = 0
        call_count = 0
        cache_keys: set[str] = set()
        last_analysis_at = None
        for row in rows:
            generated_at = _parse_iso_timestamp(_string_value(row.get("generated_at")))
            if generated_at is not None:
                if last_analysis_at is None or generated_at > last_analysis_at:
                    last_analysis_at = generated_at
                if generated_at.date() == today:
                    daily_cost += _float_value(row.get("cost_usd"), 0.0)
                    recent_analyses += 1
            call_count += int(_float_value(row.get("call_count_delta"), 1.0))
            cache_key = _string_value(row.get("cache_key"))
            if cache_key:
                cache_keys.add(cache_key)
        return {
            "daily_cost": round(daily_cost, 4),
            "call_count": call_count,
            "cache_size": len(cache_keys),
            "recent_analyses": recent_analyses,
            "last_analysis_at": last_analysis_at.isoformat() if last_analysis_at is not None else None,
        }

    async def council_status(self) -> dict[str, object]:
        decisions = await self._recent_council_rows(limit=50)
        total = await CouncilDecisionRecord.all().count()
        approved = await CouncilDecisionRecord.filter(decision="approve").count()
        rejected = await CouncilDecisionRecord.filter(decision="reject").count()
        vetoed = await CouncilDecisionRecord.filter(decision="veto").count()
        recent = [
            {
                "id": int(row.id),
                "symbol": row.signal.symbol if row.signal is not None else None,
                "decision": row.decision,
                "confidence": row.confidence,
                "buy_votes": row.buy_votes,
                "vetoed": row.decision == "veto",
                "created_at": row.created_at.isoformat() if row.created_at is not None else None,
            }
            for row in decisions[:10]
        ]
        return {
            "enabled": True,
            "voters": ["risk_engine", "market_regime", "signal_engine", "ml_engine", "data_fabric"],
            "required_approvals": 3,
            "stats": {
                "total": total,
                "approved": approved,
                "rejected": rejected,
                "vetoed": vetoed,
            },
            "min_votes": 3,
            "min_confidence": 0.55,
            "risk_veto": True,
            "llm_enabled": True,
            "recent_decisions": recent,
            "recent": recent,
            "generated_at": _iso_now(),
        }

    async def outcomes_status(self) -> dict[str, object]:
        perf = await self._performance_summary()
        return {
            "total": perf["total_trades"],
            "total_trades": perf["total_trades"],
            "wins": perf["wins"],
            "losses": perf["losses"],
            "win_rate": round(perf["win_rate"], 4),
            "total_pnl": round(perf["total_pnl"], 4),
            "_ts": _iso_now(),
            "generated_at": _iso_now(),
        }

    async def risk_status(self, container: Container) -> dict[str, object]:
        perf = await self._performance_summary()
        account = await self._safe_account(container)
        runtime_configs = RuntimeConfigService()
        max_daily_loss = await runtime_configs.resolve_float("risk.max_daily_loss_pct")
        risk_per_trade = await runtime_configs.resolve_float("risk.risk_per_trade_pct")
        max_position = await runtime_configs.resolve_float("risk.max_position_pct")
        max_open_positions = await runtime_configs.resolve("risk.max_open_positions")
        equity = _float_value(account.get("equity"), 0.0)
        cash = _float_value(account.get("cash"), equity)
        last_equity = max(_float_value(account.get("last_equity"), cash), 1.0)
        daily_pnl = round(equity - last_equity, 2)
        daily_pnl_pct = round((daily_pnl / last_equity) * 100.0, 2) if last_equity > 0.0 else 0.0
        return {
            "status": "HALTED" if container.trading_halted else "OK",
            "trading_halted": container.trading_halted,
            "halted": container.trading_halted,
            "reason": container.trading_halt_reason,
            "halt_reason": container.trading_halt_reason,
            "halted_at": container.trading_halted_at,
            "max_daily_loss_pct": _float_value(max_daily_loss.value),
            "max_position_risk_pct": _float_value(risk_per_trade.value),
            "max_position_pct": _float_value(max_position.value),
            "max_open_positions": _as_int(max_open_positions.value),
            "config_origin": {
                "max_daily_loss_pct": max_daily_loss.origin,
                "max_position_risk_pct": risk_per_trade.origin,
                "max_position_pct": max_position.origin,
                "max_open_positions": max_open_positions.origin,
            },
            "trades_today": await self._today_closed_trades(),
            "wins_today": await self._today_outcomes("win"),
            "losses_today": await self._today_outcomes("loss"),
            "daily_pnl": daily_pnl,
            "daily_pnl_pct": daily_pnl_pct,
            "max_drawdown": 0.25,
            "signals_pending": await self._pending_signals(),
            "queue": container.queue_bus.snapshot_sizes(),
            "generated_at": _iso_now(),
            "score": round((perf["win_rate"] * 100.0) - max(0.0, perf["pnl_volatility"] * 10.0), 2),
            "level": "LOW RISK" if perf["win_rate"] >= 0.5 else "ELEVATED",
            "daily_limit_used": round(abs(min(daily_pnl_pct, 0.0)), 2),
            "exposure": round(min(100.0, (await self._open_symbol_count()) * 12.5), 2),
        }

    async def _performance_summary(self) -> _PerformanceSummary:
        try:
            async with UnitOfWork() as uow:
                repo = TradeOutcomesRepository(connection=uow.connection)
                outcomes = await repo.list_recent(limit=800)
        except Exception:
            outcomes = []
        pnl_values = [float(item.pnl_pct) for item in outcomes if item.pnl_pct is not None]
        wins = sum(1 for item in outcomes if item.outcome.value == "win")
        losses = sum(1 for item in outcomes if item.outcome.value == "loss")
        total = len(outcomes)
        win_rate = (wins / total) if total > 0 else 0.0
        avg_pnl_pct = (sum(pnl_values) / len(pnl_values)) if pnl_values else 0.0
        total_pnl = sum(pnl_values)
        return {
            "total_trades": total,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "avg_pnl_pct": avg_pnl_pct,
            "total_pnl": total_pnl,
            "pnl_values": pnl_values,
            "pnl_volatility": _stddev(pnl_values),
        }

    async def _performance_by_symbol(self) -> dict[str, dict[str, float | int]]:
        try:
            async with UnitOfWork() as uow:
                rows = await TradeOutcomeRecord.all().prefetch_related("signal").using_db(uow.connection).order_by("-created_at").limit(800)
        except Exception:
            return {}
        result: dict[str, dict[str, float | int]] = {}
        for row in rows:
            symbol = row.symbol.upper()
            item = result.setdefault(symbol, {"wins": 0, "trades": 0, "pnl_sum": 0.0})
            item["trades"] = int(item["trades"]) + 1
            if row.outcome == "win":
                item["wins"] = int(item["wins"]) + 1
            item["pnl_sum"] = float(item["pnl_sum"]) + float(row.pnl_pct or 0.0)
        for symbol, item in result.items():
            trades = int(item["trades"])
            wins = int(item["wins"])
            pnl_sum = float(item["pnl_sum"])
            item["win_rate"] = (wins / trades) if trades > 0 else 0.0
            item["avg_pnl_pct"] = (pnl_sum / trades) if trades > 0 else 0.0
        return result

    async def _safe_quote(self, container: Container, symbol: str) -> dict[str, object]:
        try:
            quote = await container.broker.get_quote(symbol.upper())
            return {str(key): value for key, value in quote.items()}
        except Exception:
            return {"symbol": symbol.upper(), "bid": None, "ask": None, "last": None, "error": "quote_unavailable"}

    async def _safe_account(self, container: Container) -> dict[str, object]:
        try:
            account = await container.broker.get_account()
            return {str(key): value for key, value in account.items()}
        except Exception:
            return {"equity": 0.0, "cash": 0.0, "buying_power": 0.0, "error": "account_unavailable"}

    async def _held_symbols(self, container: Container) -> list[str]:
        try:
            positions = await container.broker.get_positions()
        except Exception:
            return []
        symbols: list[str] = []
        for item in positions:
            raw = item.get("symbol")
            if isinstance(raw, str) and raw:
                symbols.append(raw.upper())
        return symbols

    async def _recent_signal_scores(self, *, limit: int) -> list[float]:
        try:
            async with UnitOfWork() as uow:
                repo = SignalsRepository(connection=uow.connection)
                signals = await repo.list_recent(limit=limit)
        except Exception:
            return []
        return [float(signal.score) for signal in signals]

    async def _today_closed_trades(self) -> int:
        today = date.today().isoformat()
        try:
            async with UnitOfWork() as uow:
                rows = await TradeOutcomeRecord.filter(created_at__gte=datetime.fromisoformat(f"{today}T00:00:00+00:00")).using_db(uow.connection).count()
        except Exception:
            return 0
        return int(rows)

    async def _today_outcomes(self, outcome: str) -> int:
        today = date.today().isoformat()
        try:
            async with UnitOfWork() as uow:
                rows = await TradeOutcomeRecord.filter(
                    created_at__gte=datetime.fromisoformat(f"{today}T00:00:00+00:00"),
                    outcome=outcome,
                ).using_db(uow.connection).count()
        except Exception:
            return 0
        return int(rows)

    async def _pending_signals(self) -> int:
        try:
            async with UnitOfWork() as uow:
                repo = SignalsRepository(connection=uow.connection)
                return await repo.count_pending()
        except Exception:
            return 0

    async def _open_symbol_count(self) -> int:
        try:
            async with UnitOfWork() as uow:
                repo = TradeOutcomesRepository(connection=uow.connection)
                symbols = await repo.list_open_symbols()
        except Exception:
            return 0
        return len(symbols)

    async def _recent_council_rows(self, *, limit: int) -> list[CouncilDecisionRecord]:
        try:
            rows = await CouncilDecisionRecord.all().select_related("signal").order_by("-created_at").limit(limit)
        except Exception:
            return []
        return list(rows)

    async def _ml_evaluations(self, *, limit: int) -> list[dict[str, JSONValue]]:
        results = await self._artifact_rows(prefix="ml.evaluate", limit=limit)
        return results

    async def _artifact_rows(self, *, prefix: str, limit: int) -> list[dict[str, JSONValue]]:
        try:
            async with UnitOfWork() as uow:
                repo = AuditLogsRepository(connection=uow.connection)
                rows = await repo.list_recent_by_prefix(prefix=prefix, limit=limit)
            payloads = [row.payload for row in rows if isinstance(row.payload, dict)]
            if payloads:
                return payloads
        except Exception:
            pass
        return [payload for event_type, payload in _MEMORY_INTEL_ARTIFACTS if event_type.startswith(prefix)][:limit]

    async def _latest_artifact(self, event_type: str) -> dict[str, JSONValue] | None:
        try:
            async with UnitOfWork() as uow:
                repo = AuditLogsRepository(connection=uow.connection)
                row = await repo.latest_by_type(event_type=event_type)
            if row is not None and isinstance(row.payload, dict):
                return row.payload
        except Exception:
            pass
        for logged_event_type, payload in _MEMORY_INTEL_ARTIFACTS:
            if logged_event_type == event_type:
                return payload
        return None

    async def _append_artifact(self, event_type: str, payload: dict[str, JSONValue]) -> None:
        _MEMORY_INTEL_ARTIFACTS.insert(0, (event_type, payload))
        try:
            async with UnitOfWork() as uow:
                await AuditLogsRepository(connection=uow.connection).append(
                    event_type=event_type,
                    payload=payload,
                    actor="intelligence_service",
                )
        except Exception:
            return

    async def _pair_correlation(self, left: str, right: str) -> float:
        left_bars, right_bars = await self._bars_for_pair(left, right)
        left_closes = [row["close"] for row in left_bars][-CORR_LOOKBACK_DAYS:]
        right_closes = [row["close"] for row in right_bars][-CORR_LOOKBACK_DAYS:]
        if left_closes and right_closes:
            left_returns = self._returns(left_closes)
            right_returns = self._returns(right_closes)
            return pearson_correlation(left_returns, right_returns)
        # deterministic fallback only when live history is unavailable
        left_sector = sector_for_symbol(left)
        right_sector = sector_for_symbol(right)
        if left_sector == right_sector:
            return 0.82
        if "INDEX" in {left_sector, right_sector}:
            return 0.68
        return 0.34

    async def _bars_for_pair(self, left: str, right: str) -> tuple[list[_BarRow], list[_BarRow]]:
        return (
            await self._market_bars(left, range_name="6mo"),
            await self._market_bars(right, range_name="6mo"),
        )

    async def _market_bars(self, symbol: str, *, range_name: str) -> list[_BarRow]:
        bars = await MarketDataService().fetch_daily_bars(symbol, range_name=range_name)
        rows: list[_BarRow] = []
        for row in bars:
            close = _float_value(row.get("close"))
            if close <= 0.0:
                continue
            rows.append(
                {
                    "close": close,
                    "volume": _as_int(row.get("volume")),
                    "timestamp": str(row.get("timestamp", "")),
                }
            )
        return rows

    async def _watchlist_near_earnings(self) -> int:
        watchlist = ("AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "GOOGL", "META", "AMD")
        async def _check(symbol: str) -> bool:
            try:
                payload = await asyncio.wait_for(self.earnings_check(symbol), timeout=2.5)
            except Exception:
                return False
            return bool(payload.get("near_earnings", False))

        results = await asyncio.gather(*(_check(symbol) for symbol in watchlist), return_exceptions=False)
        return sum(1 for value in results if value)

    async def _correlation_universe_symbols(self, *, limit: int) -> list[str]:
        symbols = ["AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "GOOGL", "META", "AMD", "SPY", "QQQ", "GLD", "LIT"]
        return symbols[:limit]

    async def _recent_outcomes(self, *, limit: int) -> list[TradeOutcome]:
        try:
            async with UnitOfWork() as uow:
                return await TradeOutcomesRepository(connection=uow.connection).list_recent(limit=limit)
        except Exception:
            return []

    async def _recent_day_trade_count(self) -> int:
        cutoff = datetime.now(timezone.utc) - timedelta(days=7)
        try:
            rows = await TradeOutcomeRecord.all().order_by("-closed_at", "-created_at").limit(400)
        except Exception:
            outcomes = await self._recent_outcomes(limit=400)
            return recent_day_trade_count(outcomes)
        count = 0
        for row in rows:
            if row.closed_at is None or row.created_at is None:
                continue
            if row.outcome not in {"win", "loss", "breakeven"}:
                continue
            if row.closed_at < cutoff:
                continue
            if row.created_at.date() == row.closed_at.date():
                count += 1
        return count

    def _volume_ratio(self, volumes: list[int]) -> float:
        if len(volumes) < 21:
            return 0.0
        current = volumes[-1]
        history = [value for value in volumes[-21:-1] if value > 0]
        if current <= 0 or not history:
            return 0.0
        average = sum(history) / len(history)
        return 0.0 if average <= 0 else current / average

    def _permit_sources(self, source_events: list[dict[str, object]]) -> dict[str, str]:
        statuses = {"wa": "idle", "nt": "idle", "nsw": "idle", "qld": "idle"}
        if not source_events:
            return statuses
        for event in source_events:
            source = str(event.get("source", "")).lower()
            headline = str(event.get("headline", "")).lower()
            if "wa" in headline or "western australia" in headline:
                statuses["wa"] = "ok"
            if "nt" in headline or "northern territory" in headline:
                statuses["nt"] = "ok"
            if "nsw" in headline or "new south wales" in headline:
                statuses["nsw"] = "ok"
            if "qld" in headline or "queensland" in headline:
                statuses["qld"] = "ok"
            if source in {"fallback_permits", "ausmine", "google_news", "alpaca_news", "newsfeed_intel"} and all(value == "idle" for value in statuses.values()):
                statuses["wa"] = "ok"
        return statuses

    def _regime_score(self, regime: str) -> int:
        if regime == "BULL":
            return 78
        if regime == "BEAR":
            return 26
        if regime == "CRISIS":
            return 10
        return 52

    def _returns(self, closes: list[float]) -> list[float]:
        values: list[float] = []
        for index in range(1, len(closes)):
            previous = closes[index - 1]
            current = closes[index]
            if previous <= 0.0:
                continue
            values.append((current / previous) - 1.0)
        return values


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _object_dict(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return {str(key): item for key, item in value.items()}
    return {}


def _json_payload(value: object) -> dict[str, JSONValue]:
    if not isinstance(value, dict):
        return {}
    return {str(key): _json_value(item) for key, item in value.items()}


def _json_value(value: object) -> JSONValue:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, tuple):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    return str(value)


def _float_value(value: object, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return float(value)
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: object, default: int = 0) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if not isinstance(value, str):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _stddev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean_value = sum(values) / len(values)
    variance = sum((value - mean_value) ** 2 for value in values) / len(values)
    return sqrt(variance)


def _read_json_file(path: Path) -> dict[str, JSONValue] | list[JSONValue] | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _load_pickled_model(path: Path) -> object:
    with path.open("rb") as handle:
        return pickle.load(handle)


def _predict_probability(model: object, vector: list[float]) -> float | None:
    payload = [vector]
    try:
        import numpy as np  # type: ignore

        payload = np.array([vector], dtype=float)
    except Exception:
        payload = [vector]
    if hasattr(model, "predict_proba"):
        result = model.predict_proba(payload)
        if result is None:
            return None
        row = result[0]
        if isinstance(row, (list, tuple)) and len(row) >= 2:
            return float(row[1])
        if hasattr(row, "__getitem__"):
            try:
                return float(row[1])
            except Exception:
                return float(row[0])
    if hasattr(model, "predict"):
        result = model.predict(payload)
        if result is None:
            return None
        first = result[0] if isinstance(result, (list, tuple)) else result
        return float(first)
    return None


def _normalize_percentage(value: object) -> float | None:
    numeric = _float_value(value, default=-1.0)
    if numeric < 0.0:
        return None
    if numeric <= 1.0:
        numeric *= 100.0
    return round(numeric, 4)


def _normalize_ratio(value: object) -> float | None:
    numeric = _float_value(value, default=-1.0)
    if numeric < 0.0:
        return None
    if numeric > 1.0 and numeric <= 100.0:
        numeric /= 100.0
    return round(numeric, 4)


def _string_value(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def _parse_iso_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _pct_move(start: float, end: float) -> float:
    if start <= 0.0:
        return 0.0
    return ((end / start) - 1.0) * 100.0


def _approx_rsi(closes: list[float]) -> float:
    if len(closes) < 2:
        return 50.0
    gains = 0.0
    losses = 0.0
    for index in range(1, len(closes)):
        delta = closes[index] - closes[index - 1]
        if delta > 0:
            gains += delta
        else:
            losses += abs(delta)
    if losses == 0.0:
        return 100.0 if gains > 0.0 else 50.0
    relative_strength = gains / losses
    return 100.0 - (100.0 / (1.0 + relative_strength))


def _ml_label(score: float) -> str:
    if score >= 0.6:
        return "bullish"
    if score <= 0.4:
        return "bearish"
    return "neutral"
