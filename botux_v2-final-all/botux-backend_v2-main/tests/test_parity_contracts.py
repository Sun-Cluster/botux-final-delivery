from __future__ import annotations

import asyncio
import os
import time
from functools import partial
from pathlib import Path

from fastapi.testclient import TestClient
from tortoise import Tortoise

from app.services.registry.seeder import seed_registry
from main import app

_TEST_DB_FILE = Path("/tmp/botux_reference_parity_contracts.sqlite3")


async def _init_memory_db() -> None:
    if _TEST_DB_FILE.exists():
        _TEST_DB_FILE.unlink()
    await Tortoise.init(
        config={
            "connections": {"default": f"sqlite://{_TEST_DB_FILE}"},
            "apps": {"models": {"models": ["src.db.models"], "default_connection": "default"}},
            "use_tz": True,
            "timezone": "UTC",
        },
        _enable_global_fallback=True,
    )
    await Tortoise.generate_schemas(safe=True)


async def _close_memory_db() -> None:
    await Tortoise.close_connections()
    if _TEST_DB_FILE.exists():
        _TEST_DB_FILE.unlink()


def _set_env() -> None:
    os.environ["BOTUX_SKIP_DB_INIT"] = "1"
    os.environ["BOTUX_DB_URI"] = "postgres://botux:botux@127.0.0.1:5432/botux"


def _seed_registry_sync(client: TestClient) -> None:
    client.portal.call(partial(seed_registry, mode="repair"))


def test_parity_contract_matrix() -> None:
    _set_env()
    asyncio.run(_init_memory_db())
    try:
        with TestClient(app) as client:
            _seed_registry_sync(client)
            requests: list[tuple[str, str, dict | None]] = [
                ("GET", "/api/regime", None),
                ("GET", "/api/regime/status", None),
                ("GET", "/api/correlation/status", None),
                ("GET", "/api/correlation/matrix", None),
                ("GET", "/api/correlation/check/AAPL", None),
                ("GET", "/api/filters/status", None),
                ("GET", "/api/filters/check/AAPL", None),
                ("GET", "/api/pdt/status", None),
                ("GET", "/api/earnings/check/AAPL", None),
                ("GET", "/api/permits/status", None),
                ("GET", "/api/ml/status", None),
                ("GET", "/api/ml/scores", None),
                ("POST", "/api/ml/evaluate", None),
                ("GET", "/api/llm/status", None),
                ("GET", "/api/council/status", None),
                ("GET", "/api/outcomes/status", None),
                ("GET", "/api/bots/fleet", None),
                ("GET", "/api/bot/fleet", None),
                ("GET", "/api/bots/scorecards", None),
                ("GET", "/api/bots/turbo/detail", None),
                ("GET", "/api/bots/turbo/scorecard", None),
                ("POST", "/api/bots/turbo/promote", None),
                ("POST", "/api/bots/turbo/demote", None),
                ("POST", "/api/bots/turbo/enable", None),
                ("POST", "/api/bots/turbo/disable", None),
                ("POST", "/api/strategies/strat_turbo_v1/promote", None),
                ("POST", "/api/strategies/strat_turbo_v1/demote", None),
                ("POST", "/api/strategies/strat_turbo_v1/retire", None),
                ("POST", "/api/factory/candidate/cand01/admit-as-strategy", None),
                ("GET", "/api/tradecopy/status", None),
                ("GET", "/api/tradecopy/scan", None),
                ("POST", "/api/tradecopy/scan/run", None),
                ("GET", "/api/copycat/status", None),
                ("GET", "/api/copycat/scan", None),
                ("POST", "/api/copycat/scan/run", None),
                ("GET", "/api/options/status", None),
                ("GET", "/api/options/scan", None),
                ("POST", "/api/options/scan/run", None),
                ("GET", "/api/gambler/status", None),
                ("GET", "/api/gambler/scan", None),
                ("POST", "/api/gambler/scan/run", None),
                ("GET", "/api/swingtrade/status", None),
                ("GET", "/api/swingtrade/scan", None),
                ("POST", "/api/swingtrade/scan/run", None),
                ("GET", "/api/drifter/status", None),
                ("GET", "/api/drifter/scan", None),
                ("POST", "/api/drifter/scan/run", None),
                ("GET", "/api/miner/status", None),
                ("POST", "/api/miner/scan", None),
                ("GET", "/api/nugget/status", None),
                ("GET", "/api/nugget/scan", None),
                ("GET", "/api/nugget/signals", None),
                ("GET", "/api/ausmining/status", None),
                ("GET", "/api/commodities", None),
                ("GET", "/permits", None),
                ("GET", "/api/execution/tape", None),
                ("GET", "/api/exit-guard/status", None),
                ("POST", "/api/positions/close", None),
                ("GET", "/orders", None),
                ("POST", "/orders/buy", {"symbol": "AAPL", "quantity": 1}),
                ("POST", "/orders/sell", {"symbol": "AAPL", "quantity": 1}),
                ("GET", "/positions", None),
                ("GET", "/watchlist", None),
                ("GET", "/portfolio/history", None),
                ("GET", "/api/audit", None),
                ("GET", "/api/tuning", None),
                ("GET", "/api/arena", None),
                ("GET", "/api/shadow", None),
                ("GET", "/api/doctor/fixes", None),
                ("POST", "/api/cache/clear", None),
                ("GET", "/bars/AAPL", None),
                ("GET", "/api/ecosystem", None),
                ("GET", "/api/fleet/edge-status", None),
                ("GET", "/api/fleet/allocation", None),
                ("GET", "/api/fleet/risk", None),
            ]
            for method, path, payload in requests:
                if method == "GET":
                    resp = client.get(path)
                else:
                    resp = client.post(path, json=payload) if payload is not None else client.post(path)
                assert resp.status_code == 200, f"{method} {path}: {resp.status_code} {resp.text[:200]}"
    finally:
        asyncio.run(_close_memory_db())


def test_parity_shape_locks() -> None:
    _set_env()
    asyncio.run(_init_memory_db())
    try:
        with TestClient(app) as client:
            _seed_registry_sync(client)
            regime = client.get("/api/regime").json()
            assert {"regime", "multiplier", "vix", "trend"}.issubset(regime.keys())

            ml_status = client.get("/api/ml/status").json()
            assert {"status", "sample_size", "model_version"}.issubset(ml_status.keys())

            fleet = client.get("/api/bots/fleet").json()
            assert "fleet" in fleet and isinstance(fleet["fleet"], list)

            ecosystem = client.get("/api/ecosystem").json()
            assert {"bots", "strategies", "runtime", "performance"}.issubset(ecosystem.keys())

            bot_improvement = client.get("/api/monitor/bot_improvement").json()
            assert "bots" in bot_improvement and isinstance(bot_improvement["bots"], list)

            execution_quality = client.get("/api/monitor/execution_quality").json()
            assert "bots" in execution_quality and isinstance(execution_quality["bots"], list)

            signal_quality = client.get("/api/monitor/signal_quality").json()
            assert "sources" in signal_quality and isinstance(signal_quality["sources"], list)

            control_overview = client.get("/api/control-plane/overview").json()
            assert {"broker_state", "active_tasks", "critical_alerts"}.issubset(control_overview.keys())

            workers = client.get("/runtime/workers").json()
            assert "workers" in workers and "queue" in workers

            scheduler = client.get("/runtime/scheduler").json()
            assert "scheduler" in scheduler
    finally:
        asyncio.run(_close_memory_db())


def test_behavior_scan_services_persist_artifacts() -> None:
    _set_env()
    asyncio.run(_init_memory_db())
    try:
        with TestClient(app) as client:
            _seed_registry_sync(client)
            scout_scan = client.post("/api/scout/scan")
            assert scout_scan.status_code == 200
            scout_payload = scout_scan.json()
            assert scout_payload["total_items"] > 0
            assert scout_payload["theses_count"] > 0

            scout_items = client.get("/api/scout/items")
            assert scout_items.status_code == 200
            assert scout_items.json()["count"] > 0

            scout_theses = client.get("/api/scout/theses")
            assert scout_theses.status_code == 200
            assert scout_theses.json()["count"] > 0

            bridge = client.post("/api/monitor/scout_bridge")
            assert bridge.status_code == 200
            bridge_payload = bridge.json()
            assert bridge_payload["triggered"] is True
            assert "bridged" in bridge_payload

            news_scan = client.post("/api/news/scan")
            assert news_scan.status_code == 200
            news_payload = news_scan.json()
            assert news_payload["articles_stored"] > 0
            assert "dispatch" in news_payload

            news = client.get("/api/news")
            assert news.status_code == 200
            assert news.json()["count"] > 0

            time.sleep(0.1)
            signals = client.get("/api/signals")
            assert signals.status_code == 200
            signals_payload = signals.json()
            assert signals_payload["truth_source"] == "signals_db"
            if signals_payload["count"] == 0:
                assert signals_payload["degraded"] is True
            else:
                assert any(row.get("lane_hint") == "news" for row in signals_payload["signals"])
                assert any(
                    row.get("source") in {
                        "alpaca_news",
                        "google_news",
                        "scout_thesis_watchlist_momentum",
                        "scout_thesis_macro_regime",
                        "scout_thesis_cross_asset",
                    }
                    for row in signals_payload["signals"]
                )

            tradecopy_run = client.post("/api/tradecopy/scan/run")
            assert tradecopy_run.status_code == 200
            assert "candidates" in tradecopy_run.json()

            options_run = client.post("/api/options/scan/run")
            assert options_run.status_code == 200
            assert "candidates" in options_run.json()

            swing_run = client.post("/api/swingtrade/scan/run")
            assert swing_run.status_code == 200
            assert "candidates" in swing_run.json()

            miner_run = client.post("/api/miner/scan")
            assert miner_run.status_code == 200
            assert "signals" in miner_run.json()

            tradecopy_scan = client.get("/api/tradecopy/scan")
            assert tradecopy_scan.status_code == 200
            assert tradecopy_scan.json()["count"] >= 0
    finally:
        asyncio.run(_close_memory_db())


def test_behavior_intelligence_services_shape_and_semantics() -> None:
    _set_env()
    asyncio.run(_init_memory_db())
    try:
        with TestClient(app) as client:
            _seed_registry_sync(client)
            regime = client.get("/api/regime")
            assert regime.status_code == 200
            regime_payload = regime.json()
            assert regime_payload["regime"] in {"BULL", "BEAR", "NEUTRAL", "CRISIS"}
            assert "should_trade" in regime_payload

            regime_status = client.get("/api/regime/status")
            assert regime_status.status_code == 200
            assert {"regime", "score", "sizing_mult"}.issubset(regime_status.json().keys())

            filters = client.get("/api/filters/check/AAPL")
            assert filters.status_code == 200
            filters_payload = filters.json()
            assert {"allowed", "failed_filters", "metrics"}.issubset(filters_payload.keys())

            pdt = client.get("/api/pdt/status")
            assert pdt.status_code == 200
            assert {"day_trades_used", "day_trades_limit", "can_trade"}.issubset(pdt.json().keys())

            ml_scores = client.get("/api/ml/scores")
            assert ml_scores.status_code == 200
            ml_scores_payload = ml_scores.json()
            assert ml_scores_payload["count"] > 0

            ml_eval = client.post("/api/ml/evaluate?symbol=NVDA")
            assert ml_eval.status_code == 200
            ml_eval_payload = ml_eval.json()
            assert ml_eval_payload["label"] in {"bullish", "neutral", "bearish"}

            council = client.get("/api/council/status")
            assert council.status_code == 200
            assert {"stats", "recent_decisions", "required_approvals"}.issubset(council.json().keys())

            outcomes = client.get("/api/outcomes/status")
            assert outcomes.status_code == 200
            assert "total_pnl" in outcomes.json()

            risk = client.get("/api/risk/status")
            assert risk.status_code == 200
            assert {"max_daily_loss_pct", "max_position_risk_pct", "level"}.issubset(risk.json().keys())
    finally:
        asyncio.run(_close_memory_db())
