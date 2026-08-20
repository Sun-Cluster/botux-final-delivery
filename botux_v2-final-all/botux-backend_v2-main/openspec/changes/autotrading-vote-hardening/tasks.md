## 1. Parity Hardening

- [x] 1.1 Fix parity regressions in compatibility surfaces (`/api/bot/fleet` alias DI and news scan import bug).
- [x] 1.2 Add execution freshness + price-drift guardrails in shared execution path.
- [ ] 1.3 Add deterministic tests for stale signal and price-drift rejection semantics.

## 2. Vote/Risk Observability

- [x] 2.1 Emit structured council decision detail logs (decision + votes + failures) with signal trace id.
- [x] 2.2 Emit explicit risk failure logs with normalized gate payload fields.
- [ ] 2.3 Add query/report helper endpoint for vote/risk forensic timeline.

## 3. Legacy Module Rationalization

- [x] 3.1 Introduce `api/routers/compat/*` module group and move compatibility handlers incrementally.
- [x] 3.2 Keep stable facade imports until downstream callsites/tests are migrated.
- [x] 3.3 Rename scheduler job names from `legacy.*` to domain-aligned names with temporary alias mapping.

## 4. Schema Flattening Governance

- [x] 4.1 Inventory JSON fields and classify `must-flatten` vs `keep-json` with rationale.
- [x] 4.2 Implement flatten batch-1 migration (high-frequency query fields first) + backfill.
- [x] 4.3 Add migration verification tests and rollback proof scripts.

## 5. Migration Strategy Decision

- [ ] 5.1 Run staging dry-run with full migration chain.
- [ ] 5.2 Decide keep-chain vs reset-from-zero based on cutover evidence, not assumption.
