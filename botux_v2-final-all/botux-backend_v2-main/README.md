# BOTUX Refactor Workspace

This folder is the isolated rebuild workspace for backend refactor.

Rules:

- New implementation lives only under `botux/`.
- Do not edit source code under `botux-backend/` while refactor is in progress.
- Use `botux-backend/` as read-only reference reference for behavior parity.
- Follow the canonical docs in `docs/PROJECT_GUIDE.md` and `docs/PROJECT_STATUS.md`.
- Stack target: `asyncio` + Tortoise ORM + PostgreSQL.

Start here:

1. `docs/PROJECT_GUIDE.md`
2. `docs/PROJECT_STATUS.md`
3. `docs/runbooks/DB_MIGRATION_RUNBOOK.md`
4. `docs/checklists/REFERENCE_FLOW_REIMPLEMENTATION_CHECKLIST.md` (behavior-complete reference reimplementation backlog)
5. `docs/REFERENCE_REIMPLEMENTATION_GUIDE.md` (reference refs + implementation standards)
6. `docs/LANE_INTEL_BOT_MAP.md` (intel -> lane -> bot map + 5 lane operation models)

Target architecture scaffold already exists under `src/` and will be filled incrementally.

Quickstart:

```bash
cd botux
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
docker compose up -d postgres
make db-migrate-init
make db-migrate-up
make test-migration
make test
make api
```

Docker Compose:

```bash
cd botux
docker compose up --build
```

Services:

- Dashboard: `http://localhost:3001`
- API: `http://localhost:8001`
- Healthcheck: `http://localhost:8001/health`

Notes:

- `docker compose up` now starts PostgreSQL, BOTUX API, and the Next.js dashboard together.
- Docker Compose now loads variables from local `.env` via `env_file`, and also mounts that file into both app containers at `/app/.env`.
- FastAPI therefore reads the same file through `load_dotenv(...)`, while the dashboard also has the file available inside the container if runtime env-based config is added later.
- The API container still overrides a few Docker-specific values in compose:
  - `BOTUX_DB_URI=postgres://botux:botux@postgres:5432/botux`
  - `BOTUX_API_HOST=0.0.0.0`
  - `BOTUX_API_PORT=8000`
  - `BOTUX_DB_AUTO_SCHEMA=1`
  - `IBKR_HOST=host.docker.internal` so the container can reach TWS/IB Gateway running on the host machine
- The dashboard proxies `/api/v2/*` to the internal `api` service, so opening the dashboard from another machine via the host IP still works.

Config note:

- Database uses a single required env var: `BOTUX_DB_URI` (example in `.env.example`).
- Runtime logging uses `loguru` and reads level from `BOTUX_LOG_LEVEL` (for example: `DEBUG`, `INFO`, `WARNING`, `ERROR`).
  - Log format: `<Time> <Level> [<module>:<line>]: <content>`
- Broker mode is configured by `BOTUX_BROKER_MODE`:
  - `paper` / `alpaca` -> `AlpacaAdapter`
  - `ibkr` -> `IbkrAdapter`
- Real broker connectivity is opt-in:
  - Alpaca: set `BOTUX_ALPACA_REAL_ENABLED=1` plus `ALPACA_API_KEY` / `ALPACA_SECRET_KEY`
  - IBKR: set `BOTUX_IBKR_REAL_ENABLED=1` plus `IBKR_HOST` / `IBKR_PORT` / `IBKR_CLIENT_ID` and ensure `ib_insync` can reach Gateway/TWS
  - Without those flags/configs, adapters report disconnected and reject order placement
- Optional news/intel source envs:
  - `NEWS_API_KEY`
  - `BOTUX_DISABLE_LIVE_INTEL_FETCH` (`1` to disable live market-data fetch for intel/regime helpers)
- Optional scheduler controls:
  - Scheduler is always started by the FastAPI app.
  - `BOTUX_PORTFOLIO_SNAPSHOT_INTERVAL_SECONDS` (`0` to disable that job)
  - `BOTUX_RECONCILE_INTERVAL_SECONDS` (`0` to disable)
  - `BOTUX_NEWS_SCAN_INTERVAL_SECONDS`
  - `BOTUX_SIGNAL_BROADCAST_INTERVAL_SECONDS`
  - `BOTUX_EXECUTION_LOOP_INTERVAL_SECONDS`
  - `BOTUX_RISK_CYCLE_INTERVAL_SECONDS`
  - `BOTUX_POSITION_MONITOR_INTERVAL_SECONDS`
  - `BOTUX_SCOUT_SCAN_INTERVAL_SECONDS`
  - `BOTUX_MINER_SCAN_INTERVAL_SECONDS`

Current API surfaces:

- `GET /health`
- `GET /runtime/queues`
- `GET /runtime/metrics`
- `GET /runtime/scheduler`
- `GET /runtime/workers`
- `POST /signals/ingest?enqueue=true`
- `POST /signals/process-pending?enqueue=true&limit=100&quantity=1.0`
- `POST /signals/{signal_id}/requeue?reason=manual_requeue`
- `POST /portfolio/snapshot`
- `POST /reconcile/run`
- `GET /reconcile/status`
- `GET /reconcile/report`

Core compatibility (`/api/*`) currently migrated:

- health/account/positions
- portfolio allocation/equity
- trades/trades today
- executor status + run
- signals reprocess
- brokers status + broker account
- reconcile status/report/run
- risk status + emergency halt/resume
- monitor status/summary
- IBKR status + reconnect
- bot profiles + strategy registry read/update compatibility
- events feed + market/scout compatibility endpoints
- control-plane/ops/monitor compatibility endpoints + SSE event stream
- reference route-map parity batch for risk/regime/ml/bot-lifecycle/lane-scan/trading-advanced/fleet-analytics endpoints
- broker adapter layer now includes real Alpaca REST integration and real IBKR lazy-connect integration with paper fallback

Migration commands:

- `make db-migrate-init` (initialize migration package for configured apps)
- `make db-migrate-new MIGRATION_NAME=add_orders_index`
- `make db-migrate-up`
- `make db-migrate-down APP_LABEL=models TARGET=0001_initial`
- `make db-migrate-history`
- `make db-migrate-heads`
- `make db-migrate-sql APP_LABEL=models MIGRATION=0001_initial`
- `make verify-foundation` (docker up + healthcheck + migrate + expected-table verification report)
- `make test-migration` (pytest integration test for foundation/migration flow)

Inventory + import staging commands:

- `make inventory-supabase` (generate Supabase `.table(...)` inventory)
- `make inventory-runtime` (generate JSON/JSONL runtime file inventory)
- `make inventory-all`
- `make snapshot-reference` (snapshot state-critical reference data files with checksum manifest; `snapshot-reference-compat` remains as alias)
- `make import-runtime` (dry-run staging report for runtime file imports)
- `make import-supabase` (dry-run staging report from exported Supabase snapshot JSON)
- `make reconcile-import` (merge staging reports into reconciliation report)
- `make baseline-stabilization` (capture queue depth + event-loop latency baseline)
- `make cutover-dry-run` (generate cutover readiness dry-run report)
- `make rollback-dry-run` (generate rollback dry-run report)

Note:

- Makefile uses `uv run tortoise` to avoid dependency on globally installed CLI binaries.
- Operational runbooks/checklists kept separate on purpose:
  - `docs/checklists/CUTOVER_CHECKLIST.md`
  - `docs/checklists/ROLLBACK_CHECKLIST.md`
  - `docs/runbooks/DB_MIGRATION_RUNBOOK.md`
