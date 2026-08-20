## Why

The refactor workspace has reached behavior parity, but several critical modules have grown large enough to slow delivery, increase review risk, and make regressions harder to isolate. We should refactor structure now, before new feature waves expand these hotspots further.

## What Changes

- Define enforceable decomposition rules for oversized API router and service modules.
- Split high-churn, high-size modules into smaller bounded submodules while preserving existing public API behavior.
- Introduce stable facade entrypoints so callers keep using consistent import and wiring surfaces during the transition.
- Add refactor safety checks (tests and CI validations) that prove no behavioral regressions across legacy compatibility and runtime flows.

## Capabilities

### New Capabilities
- `module-decomposition-governance`: Establish and enforce module boundaries, ownership, and acceptable file-size/complexity limits for core API/service layers.
- `refactor-safety-validation`: Provide deterministic validation gates that prove structural refactors do not change API contracts, runtime behavior, or persistence semantics.

### Modified Capabilities
- None.

## Impact

- Affected code: `src/api/routers/legacy_api_extra.py`, `src/api/routers/control_plane_compat.py`, and large service modules under `src/app/services/**`.
- Affected tests: parity contracts, runtime/service behavior tests, and any import path assumptions.
- Tooling impact: CI/check scripts may gain architecture validation and module complexity checks.
- No intended external API breaking changes; behavior should remain contract-compatible.
