## ADDED Requirements

### Requirement: Hotspot Modules SHALL Be Decomposed By Responsibility
The system SHALL decompose oversized API router and service hotspot modules into bounded submodules that each own a single primary responsibility (for example routing, mapping, orchestration, or policy evaluation).

#### Scenario: Decomposition plan exists for a hotspot module
- **WHEN** a module is identified as a refactor hotspot
- **THEN** a decomposition map defines target submodules and assigned responsibilities before extraction begins

### Requirement: Public Facade Contracts MUST Remain Stable During Extraction
The system MUST preserve existing public facade entrypoints for callers while internal logic is moved to new submodules.

#### Scenario: Caller imports unchanged facade
- **WHEN** a caller imports or invokes the existing facade entrypoint after extraction
- **THEN** behavior and interface remain compatible with pre-refactor expectations

### Requirement: Architecture Boundaries MUST Be Enforced In CI
The system MUST enforce dependency-direction and complexity constraints for refactored modules through automated CI checks.

#### Scenario: Boundary violation is introduced
- **WHEN** a change introduces a prohibited layer dependency or exceeds configured module complexity limits without an approved exception
- **THEN** CI fails the change and reports the violated rule
