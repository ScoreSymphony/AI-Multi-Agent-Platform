# Issue #76 foundation status

This slice establishes the durable accounting contract after #16 Stage-1 telemetry and #32 Control Plane became available.

## Implemented acceptance evidence

- Canonical `UsageRecord` IDs, explicit units, source/provenance and measurement-quality classification.
- Task/Run/Executor telemetry can be persisted and queried without Models or remote Workers.
- Missing metrics use `quality=unavailable` with `quantity=None`.
- Accounting ownership is separate from observability; #16 only supplies measurement inputs.
- Basic budgets compute consumed/remaining state, retain monotonic version history and emit deduplicated threshold events.
- No external billing or paid telemetry service is required.
- SQLite restart persistence and replaceable store semantics are covered.
- Current model provider usage can already be ingested as provider-reported measurements when supplied.
- Control Plane extension collections expose records, aggregates and budget state.
- Owner-scoped records, aggregates and budgets deny cross-owner disclosure at the accounting service boundary.

## Still progressive

Issue #76 should remain open after this foundation because full completion still requires the owning domains for richer Worker/Node resources (#14), storage/workspace accounting (#13/#37), complete Agent/Team/Organization attribution (#33/#87), external/browser/repository usage (#44/#74/#82), Resources UI, #75 notification adaptation and #15/#14 enforcement integration.
