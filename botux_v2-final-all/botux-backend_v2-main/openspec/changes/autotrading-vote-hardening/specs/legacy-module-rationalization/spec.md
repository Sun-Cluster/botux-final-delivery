## ADDED Requirements

### Requirement: Compatibility Surfaces SHALL Be Grouped Under Maintained Module Boundaries
The system SHALL organize compatibility handlers into cohesive module groups instead of keeping large `legacy**` hotspots.

#### Scenario: Compatibility endpoint is maintained
- **WHEN** a compatibility endpoint is updated
- **THEN** its implementation lives under a bounded `compat` module group with stable facade entrypoint

### Requirement: Legacy-Oriented Names MUST Be Phase-Renamed With Backward Safety
The system MUST support a staged rename plan that removes ambiguous `legacy.*` runtime naming while preserving behavior during transition.

#### Scenario: Scheduler/runtime identifiers are renamed
- **WHEN** job identifiers are migrated to domain-aligned names
- **THEN** compatibility aliasing preserves existing monitors/tests until migration cutover is complete
