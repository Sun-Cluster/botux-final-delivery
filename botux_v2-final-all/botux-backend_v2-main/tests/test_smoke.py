import os
import time

from fastapi.testclient import TestClient

from main import app


def test_health_endpoint() -> None:
    os.environ["BOTUX_SKIP_DB_INIT"] = "1"
    os.environ["BOTUX_DB_URI"] = "postgres://botux:botux@127.0.0.1:5432/botux"
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "ok"


def test_runtime_queue_snapshot_endpoint() -> None:
    os.environ["BOTUX_SKIP_DB_INIT"] = "1"
    os.environ["BOTUX_DB_URI"] = "postgres://botux:botux@127.0.0.1:5432/botux"
    with TestClient(app) as client:
        response = client.get("/runtime/queues")
        assert response.status_code == 200
        payload = response.json()
        assert payload["work_queue"] >= 0
        assert payload["retry_queue"] >= 0
        assert payload["dead_letter_queue"] >= 0


def test_runtime_metrics_endpoint() -> None:
    os.environ["BOTUX_SKIP_DB_INIT"] = "1"
    os.environ["BOTUX_DB_URI"] = "postgres://botux:botux@127.0.0.1:5432/botux"
    with TestClient(app) as client:
        response = client.get("/runtime/metrics")
        assert response.status_code == 200
        payload = response.json()
        assert payload["queue"]["work_queue"] >= 0
        assert payload["queue"]["retry_queue"] >= 0
        assert payload["queue"]["dead_letter_queue"] >= 0
        assert payload["event_loop_latency_ms"] >= 0
        assert payload["scheduler"]["job_count"] >= 0
        assert payload["scheduler"]["enabled"] in {True, False}

        scheduler = client.get("/runtime/scheduler")
        assert scheduler.status_code == 200
        assert "scheduler" in scheduler.json()

        workers = client.get("/runtime/workers")
        assert workers.status_code == 200
        workers_payload = workers.json()
        assert "workers" in workers_payload
        assert "queue" in workers_payload


def test_cors_preflight_allows_local_frontend() -> None:
    os.environ["BOTUX_SKIP_DB_INIT"] = "1"
    os.environ["BOTUX_DB_URI"] = "postgres://botux:botux@127.0.0.1:5432/botux"
    with TestClient(app) as client:
        response = client.options(
            "/api/news",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert response.status_code == 200
        assert response.headers["access-control-allow-origin"] == "*"


def test_core_api_compat_endpoints() -> None:
    os.environ["BOTUX_SKIP_DB_INIT"] = "1"
    os.environ["BOTUX_DB_URI"] = "postgres://botux:botux@127.0.0.1:5432/botux"
    with TestClient(app) as client:
        health = client.get("/api/health")
        assert health.status_code == 200
        assert health.json()["status"] == "ok"

        account = client.get("/api/account")
        assert account.status_code == 200
        assert "equity" in account.json()

        positions = client.get("/api/positions")
        assert positions.status_code == 200
        assert "positions" in positions.json()

        brokers = client.get("/api/brokers/status")
        assert brokers.status_code == 200
        payload = brokers.json()
        assert payload["count"] >= 1

        recon_status = client.get("/api/recon/status")
        assert recon_status.status_code == 200
        assert recon_status.json()["has_report"] is False

        recon_run = client.post("/api/recon/run")
        assert recon_run.status_code == 200
        recon_payload = recon_run.json()
        assert isinstance(recon_payload, dict)
        assert recon_payload.get("status") == "failed" or "reconciliation" in recon_payload

        risk_status = client.get("/api/risk/status")
        assert risk_status.status_code == 200
        assert "trading_halted" in risk_status.json()

        halt = client.post("/api/emergency/halt", json={"reason": "test_halt"})
        assert halt.status_code == 200
        assert halt.json()["halted"] is True

        blocked = client.post("/signals/process-pending")
        assert blocked.status_code == 200
        blocked_payload = blocked.json()
        assert blocked_payload["processed"] == 0
        assert blocked_payload["enqueued"] == 0

        monitor_summary = client.get("/api/monitor/summary")
        assert monitor_summary.status_code == 200
        assert "queue" in monitor_summary.json()

        ibkr_status = client.get("/api/ibkr/status")
        assert ibkr_status.status_code == 200
        assert "status" in ibkr_status.json()

        ibkr_reconnect = client.post("/api/ibkr/reconnect")
        assert ibkr_reconnect.status_code == 200

        execute_stub = client.post("/api/execute")
        assert execute_stub.status_code == 200

        sync_truth = client.post("/api/runtime/sync/trading-truth")
        assert sync_truth.status_code == 200
        sync_payload = sync_truth.json()
        assert "order_status_sync" in sync_payload
        assert "outcome_lifecycle_sync" in sync_payload

        risk_halt_alias = client.post("/risk/halt", json={"reason": "alias_halt"})
        assert risk_halt_alias.status_code == 200
        assert risk_halt_alias.json()["status"] == "HALTED"

        risk_resume_alias = client.post("/risk/resume")
        assert risk_resume_alias.status_code == 200
        assert risk_resume_alias.json()["status"] == "ACTIVE"

        resume = client.post("/api/emergency/resume")
        assert resume.status_code == 200
        assert resume.json()["halted"] is False

        bots_canonical = client.get("/api/bots/canonical")
        assert bots_canonical.status_code == 200
        bots_payload = bots_canonical.json()
        assert "profiles" in bots_payload

        bot_profiles = client.get("/api/bot/profiles")
        assert bot_profiles.status_code == 200
        assert "profiles" in bot_profiles.json()

        strategies = client.get("/api/strategies")
        assert strategies.status_code == 200
        assert "strategies" in strategies.json()

        bot_patch = client.patch("/api/bots/canonical/turbo", json={"notes": "smoke"})
        assert bot_patch.status_code == 200
        assert "updated" in bot_patch.json()

        strategy_patch = client.patch("/api/strategies/strat-1", json={"notes": "smoke"})
        assert strategy_patch.status_code == 200
        assert "updated" in strategy_patch.json()

        events_feed = client.get("/api/events/feed")
        assert events_feed.status_code == 200
        assert "events" in events_feed.json()

        agent_feed = client.get("/api/agent/feed")
        assert agent_feed.status_code == 200
        assert isinstance(agent_feed.json(), list)

        agents = client.get("/api/agents")
        assert agents.status_code == 200
        assert isinstance(agents.json(), list)

        news = client.get("/api/news")
        assert news.status_code == 200
        assert "news" in news.json()

        market_signals = client.get("/api/signals")
        assert market_signals.status_code == 200
        signals_payload = market_signals.json()
        assert "signals" in signals_payload
        if signals_payload["signals"]:
            first_signal = signals_payload["signals"][0]
            assert first_signal["ticker"] == first_signal["symbol"]
            assert "conf" in first_signal
            assert "confidence" in first_signal
            assert "headline" in first_signal
            assert "blocked_reason" in first_signal
            assert "reason_code" in first_signal
            assert "reason" in first_signal

        scanner = client.get("/api/scanner")
        assert scanner.status_code == 200
        assert "stocks" in scanner.json()

        scout_status = client.get("/api/scout/status")
        assert scout_status.status_code == 200
        assert "status" in scout_status.json()

        scout_items = client.get("/api/scout/items")
        assert scout_items.status_code == 200
        assert "items" in scout_items.json()

        control_overview = client.get("/api/control-plane/overview")
        assert control_overview.status_code == 200
        assert "fleet_size" in control_overview.json()

        control_runtime = client.get("/api/control-plane/runtime")
        assert control_runtime.status_code == 200
        assert "runtime_mode" in control_runtime.json()

        control_tasks = client.get("/api/control-plane/tasks")
        assert control_tasks.status_code == 200
        assert "tasks" in control_tasks.json()

        governance_locks = client.get("/api/governance/locks")
        assert governance_locks.status_code == 200
        assert "locks" in governance_locks.json()

        governance_registry = client.get("/api/governance/registry")
        assert governance_registry.status_code == 200
        assert "strategies" in governance_registry.json()

        ops_agents = client.get("/api/ops/agents")
        assert ops_agents.status_code == 200
        assert "fleet" in ops_agents.json()

        ops_state = client.get("/api/ops/state")
        assert ops_state.status_code == 200
        assert "state" in ops_state.json()

        monitor_root = client.get("/api/monitor")
        assert monitor_root.status_code == 200
        assert "status" in monitor_root.json()

        monitor_exec = client.get("/api/monitor/execution_quality")
        assert monitor_exec.status_code == 200
        monitor_exec_payload = monitor_exec.json()
        assert "total_outcomes" in monitor_exec_payload
        assert "bots" in monitor_exec_payload

        monitor_signal = client.get("/api/monitor/signal_quality")
        assert monitor_signal.status_code == 200
        monitor_signal_payload = monitor_signal.json()
        assert "total_signals" in monitor_signal_payload
        assert "sources" in monitor_signal_payload

        monitor_broker = client.get("/api/monitor/broker_health")
        assert monitor_broker.status_code == 200
        assert "brokers" in monitor_broker.json()

        monitor_shadow = client.get("/api/monitor/strategy_shadow")
        assert monitor_shadow.status_code == 200
        assert "strategies" in monitor_shadow.json()

        monitor_promote = client.get("/api/monitor/promotion_readiness")
        assert monitor_promote.status_code == 200
        assert "items" in monitor_promote.json()

        monitor_bridge = client.post("/api/monitor/scout_bridge")
        assert monitor_bridge.status_code == 200
        assert monitor_bridge.json()["triggered"] is True


def test_api_extra_compat_endpoints() -> None:
    os.environ["BOTUX_SKIP_DB_INIT"] = "1"
    os.environ["BOTUX_DB_URI"] = "postgres://botux:botux@127.0.0.1:5432/botux"
    with TestClient(app) as client:
        assert client.get("/api/regime").status_code == 200
        assert client.get("/api/correlation/status").status_code == 200
        assert client.get("/api/pdt/status").status_code == 200
        assert client.get("/api/ml/status").status_code == 200
        assert client.get("/api/llm/status").status_code == 200
        assert client.get("/api/bots/fleet").status_code == 200
        assert client.get("/api/measurement/scorecards").status_code == 200
        assert client.get("/api/tradecopy/status").status_code == 200
        assert client.get("/api/options/status").status_code == 200
        assert client.get("/api/miner/status").status_code == 200
        assert client.get("/api/evo/status").status_code == 200
        assert client.get("/api/evo/scan").status_code == 200
        assert client.get("/api/execution/tape").status_code == 200
        assert client.get("/orders").status_code == 200
        assert client.get("/positions").status_code == 200
        assert client.get("/watchlist").status_code == 200
        assert client.get("/api/audit").status_code == 200
        assert client.get("/bars/AAPL").status_code == 200
        assert client.get("/api/ecosystem").status_code == 200
        assert client.get("/api/proof/runtime").status_code == 200
        proof_auto_exits = client.get("/api/proof/auto-exits")
        assert proof_auto_exits.status_code == 200
        assert "items" in proof_auto_exits.json()
        checklist_post = client.post(
            "/api/governance/trading-change/checklist",
            json={
                "change_id": "patch-p2-3-smoke",
                "phase": "post",
                "scope": ["entry", "exit"],
                "tests_passed": ["pytest subset"],
                "runtime_evidence": ["/api/proof/runtime"],
            },
        )
        assert checklist_post.status_code == 200
        assert checklist_post.json().get("saved") is True
        checklist_list = client.get("/api/governance/trading-change/checklist")
        assert checklist_list.status_code == 200
        fleet_payload = client.get("/api/bots/fleet").json()
        assert fleet_payload.get("status_source") == "enabled+lifecycle+scheduler"
        if fleet_payload.get("fleet"):
            assert "runtime_status" in fleet_payload["fleet"][0]
