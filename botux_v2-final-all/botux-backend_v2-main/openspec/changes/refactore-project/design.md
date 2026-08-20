## Context

The project has completed core cutover and behavior parity, but several hotspot modules now combine multiple responsibilities (routing, orchestration, policy mapping, and transformation logic) in single files. This raises maintenance cost and regression risk, especially in compatibility surfaces and lane/intelligence services where logic volume is high.

Constraints:
- External API behavior and route contracts must remain stable.
- Existing runtime wiring and dependency injection patterns must continue to work.
- Refactor must preserve DB persistence semantics and state-transition rules.

## Goals / Non-Goals

**Goals:**
- Decompose oversized modules into bounded submodules organized by responsibility.
- Preserve current public contracts through stable facade entrypoints.
- Add validation gates that prove behavior parity before and after decomposition.
- Make future changes safer by enforcing architecture and complexity guardrails.

**Non-Goals:**
- No new product features or endpoint additions.
- No intentional API contract changes.
- No data model redesign beyond what is required for refactor-safe extraction.

## Decisions

1. Decompose by responsibility boundary, not by arbitrary file length.
- Decision: split modules into cohesive units (for example: route handlers, request/response mapping, policy resolution, execution orchestration).
- Rationale: responsibility boundaries minimize hidden coupling better than line-count-only splits.
- Alternative considered: split by equal file size chunks. Rejected because it preserves ambiguity and cross-chunk coupling.

2. Preserve caller stability with facade modules.
- Decision: keep top-level import/wiring entrypoints as thin facades that delegate to extracted submodules.
- Rationale: avoids widespread call-site churn and reduces migration risk.
- Alternative considered: immediate call-site rewiring to new internal modules. Rejected because it increases blast radius and review complexity.

3. Add explicit architecture guardrails in CI.
- Decision: add checks for layer dependency direction and module complexity ceilings with exception allowlists.
- Rationale: prevents hotspot regression after initial cleanup.
- Alternative considered: rely on code review only. Rejected because manual enforcement degrades over time.

4. Validate parity through existing contract suites plus focused regression checks.
- Decision: require legacy parity contracts, runtime behavior tests, and targeted endpoint/service snapshots across changed modules.
- Rationale: this project already has parity-oriented evidence; extending that system is lower risk than new one-off validation.
- Alternative considered: manual smoke verification only. Rejected because it cannot prove semantic parity for edge paths.

## Risks / Trade-offs

- [Boundary mistakes during extraction] -> Mitigation: extract incrementally behind facades and run full relevant test subsets after each extraction batch.
- [Temporary duplication across old/new internal paths] -> Mitigation: define cleanup checkpoints and remove old internal code once parity is proven.
- [CI runtime increase from extra checks] -> Mitigation: scope architecture checks to changed modules and keep heavy parity suites in scheduled/full pipelines when needed.
- [Hidden import cycles after modularization] -> Mitigation: add dependency-direction checks and fail builds on cycle detection.

## Migration Plan

1. Establish decomposition map per hotspot module (target submodules, ownership, and facade boundary).
2. Extract internal logic into submodules while preserving current facade entrypoints.
3. Run parity/behavior tests after each module extraction batch.
4. Enable architecture and complexity checks in CI as warning mode, then enforce mode after baseline stabilization.
5. Remove temporary compatibility shims that are no longer needed once validation is consistently green.

Rollback strategy:
- Keep extraction batches small and reversible.
- Revert the most recent extraction batch if parity checks fail and patch boundary defects before retrying.

## Open Questions

- Should complexity ceilings be globally fixed or tuned per layer (`api`, `app`, `infra`)?
- Which parity checks should be required on every PR vs. nightly/full validation only?
- Do we need a dedicated architectural decision record for future exception approvals?
