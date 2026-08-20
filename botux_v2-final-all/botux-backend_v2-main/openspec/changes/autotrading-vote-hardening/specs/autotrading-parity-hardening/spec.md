## ADDED Requirements

### Requirement: Execution Path MUST Enforce Signal Freshness And Price Drift Gates
The system MUST reject execution attempts when incoming signal age or reference-price drift exceeds configured safety limits.

#### Scenario: Signal is stale for its market lane
- **WHEN** execution evaluates a signal whose age is greater than the configured threshold for the lane market
- **THEN** the order is rejected before broker submission and a structured block reason is logged

#### Scenario: Signal price has drifted too far from current quote
- **WHEN** execution evaluates a signal with reference price and quote drift above configured percentage
- **THEN** the order is rejected and drift telemetry is persisted in execution metadata/logs

### Requirement: Council/Risk Decision Trail MUST Be Structured Per Signal
The system MUST persist and emit structured decision details that include council votes and risk failures per signal.

#### Scenario: Council decision is produced
- **WHEN** submit-order flow completes gate evaluation
- **THEN** structured decision detail includes decision summary, per-voter records, and failure records with traceable signal id
