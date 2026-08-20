## 1. Refactor Guardrails Setup

- [x] 1.1 Define hotspot module inventory and responsibility decomposition maps for each target file
- [x] 1.2 Add architecture dependency-direction checks for `api`, `app`, `domain`, `db`, and `infra` layers
- [x] 1.3 Add module complexity/file-size threshold checks with an explicit exception allowlist policy

## 2. Facade-Preserving Module Decomposition

- [x] 2.1 Extract `src/api/routers/legacy_api_extra.py` into bounded submodules behind a stable facade entrypoint
- [x] 2.2 Extract `src/api/routers/control_plane_compat.py` into bounded submodules behind a stable facade entrypoint
- [x] 2.3 Extract selected hotspot services under `src/app/services/**` into cohesive submodules while keeping public service interfaces stable

## 3. Refactor Safety Validation

- [x] 3.1 Define required validation matrix per refactor batch (parity contracts, runtime behavior, persistence-critical tests)
- [x] 3.2 Wire CI to enforce required validation gates for touched hotspot modules
- [x] 3.3 Capture and publish per-batch validation evidence (executed suites and outcomes)

## 4. Stabilization And Cleanup

- [ ] 4.1 Remove temporary internal shims after extracted paths are validated as parity-safe
- [x] 4.2 Tighten guardrails from warning mode to enforce mode once baseline passes are stable
- [x] 4.3 Update project docs to reflect new module boundaries and ongoing refactor governance rules
