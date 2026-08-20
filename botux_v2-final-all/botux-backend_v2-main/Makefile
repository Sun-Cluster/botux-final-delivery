.PHONY: test test-migration verify-foundation api db-migrate-init db-migrate-new db-migrate-up db-migrate-down db-migrate-history db-migrate-heads db-migrate-sql db-seed-registry inventory-supabase inventory-runtime inventory-all snapshot-reference snapshot-reference-compat import-runtime import-supabase reconcile-import baseline-stabilization cutover-dry-run rollback-dry-run check-architecture check-complexity validate-refactor

MIGRATION_NAME ?= change
TORTOISE_CONFIG ?= src.db.tortoise_orm.TORTOISE_ORM
APP_LABEL ?= models
TARGET ?= __first__
MIGRATION ?= 0001_initial
SEED_MODE ?= repair
TORTOISE_CLI ?= uv run tortoise
PYTEST ?= uv run pytest
PYTHON ?= uv run python

test:
	BOTUX_SKIP_DB_INIT=1 $(PYTEST) -q

test-migration:
	BOTUX_RUN_MIGRATION_TESTS=1 $(PYTEST) -q tests/test_migration_foundation.py

verify-foundation:
	$(PYTHON) scripts/verify_foundation.py

api:
	uvicorn src.main:app --host 0.0.0.0 --port 8000 --reload

db-migrate-init:
	$(TORTOISE_CLI) -c $(TORTOISE_CONFIG) init

db-migrate-new:
	$(TORTOISE_CLI) -c $(TORTOISE_CONFIG) makemigrations --name $(MIGRATION_NAME)

db-migrate-up:
	$(TORTOISE_CLI) -c $(TORTOISE_CONFIG) migrate

db-migrate-down:
	$(TORTOISE_CLI) -c $(TORTOISE_CONFIG) downgrade $(APP_LABEL) $(TARGET)

db-migrate-history:
	$(TORTOISE_CLI) -c $(TORTOISE_CONFIG) history $(APP_LABEL)

db-migrate-heads:
	$(TORTOISE_CLI) -c $(TORTOISE_CONFIG) heads $(APP_LABEL)

db-migrate-sql:
	$(TORTOISE_CLI) -c $(TORTOISE_CONFIG) sqlmigrate $(APP_LABEL) $(MIGRATION)

db-seed-registry:
	$(PYTHON) scripts/seed_registry.py --mode $(SEED_MODE)

inventory-supabase:
	$(PYTHON) scripts/inventory_supabase_calls.py

inventory-runtime:
	$(PYTHON) scripts/inventory_runtime_files.py

inventory-all: inventory-supabase inventory-runtime

snapshot-reference:
	$(PYTHON) scripts/snapshot_reference_sources.py

snapshot-reference-compat: snapshot-reference

import-runtime:
	$(PYTHON) scripts/import_runtime_files.py

import-supabase:
	$(PYTHON) scripts/import_supabase_snapshot.py

reconcile-import:
	$(PYTHON) scripts/reconcile_import.py

baseline-stabilization:
	$(PYTHON) scripts/capture_stabilization_baseline.py

cutover-dry-run:
	$(PYTHON) scripts/cutover_dry_run.py

rollback-dry-run:
	$(PYTHON) scripts/rollback_dry_run.py

check-architecture:
	$(PYTHON) scripts/check_architecture_boundaries.py

check-complexity:
	$(PYTHON) scripts/check_module_complexity.py

validate-refactor:
	$(PYTHON) scripts/run_refactor_validation.py
