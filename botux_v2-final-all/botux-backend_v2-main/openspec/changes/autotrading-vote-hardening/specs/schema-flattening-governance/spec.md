## ADDED Requirements

### Requirement: Query-Critical Runtime Fields MUST Be Flattened Out Of JSON
The system MUST move frequently queried runtime/governance fields from JSON blobs into typed columns.

#### Scenario: Field is high-frequency in filters or reports
- **WHEN** schema review marks a field as query-critical
- **THEN** the field is promoted to typed column with backfill migration and index strategy where needed

### Requirement: JSON Usage MUST Be Reserved For Variable Payloads
The system MUST keep JSON columns for sparse or highly variable payloads only.

#### Scenario: Field has stable semantics across records
- **WHEN** a JSON key has stable meaning and appears broadly
- **THEN** it is scheduled for flattening in a migration batch

### Requirement: Migration Reset Decision MUST Be Evidence-Based
The system MUST decide between incremental migration chain and reset-from-zero only after staged dry-run evidence.

#### Scenario: Migration strategy review
- **WHEN** cutover readiness is evaluated
- **THEN** decision includes rehearsal evidence, rollback path, and data integrity checks
