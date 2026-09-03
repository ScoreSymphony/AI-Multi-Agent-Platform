# Usage and resource accounting

Issue #76 owns durable, attributable usage/resource/cost accounting. Observability (#16) remains a separate derived operational view: it may emit measurements, but it is not the accounting source of truth.

## Foundation

The foundation introduces a provider-neutral `UsageRecord` with explicit metric type, quantity, canonical unit, timestamp/interval, attribution scope, provider/source, correlation/causation, optional cost metadata, provenance and measurement quality.

Measurement quality is always one of:

- `measured` — directly measured by platform instrumentation;
- `reported` — supplied by a subsystem/provider;
- `estimated` — derived approximation;
- `unavailable` — the metric is not available and therefore has no quantity.

`unavailable` is never converted to zero. Estimated external cost is never represented as an invoice or exact measured value.

## Telemetry ingestion

`AccountingService` structurally implements the `MeasurementSink` expected by `AccountingBridgeExporter`. Translation is an explicit whitelist. Unknown telemetry metrics are ignored instead of being silently reinterpreted.

Initial mappings cover Task/Run/Executor counts, outcomes, durations, queue wait and retry measurements. Reliable model/tool measurements are accepted progressively; model token usage is separated into input, output, total, cached and reasoning semantics so those values are never silently added together. Generic Worker/Node metadata emitted as `reported_units` is deliberately not converted into canonical accounting until #14 defines reliable resource semantics. Provider-reported usage remains `reported` and keeps provider/config provenance.

Metric ingestion uses a deterministic usage ID derived from the source `MetricRecord`, making exact repeated delivery idempotent.

## Storage and replacement

`UsageStore` is the canonical persistence boundary. The repository includes:

- `InMemoryUsageStore` for tests/composition;
- `SQLiteUsageStore` as a dependency-free durable reference implementation.

Replacing the storage provider does not change `UsageRecord`, IDs, units, quality semantics, queries or budget semantics.

## Aggregation

`AccountingService.aggregate()` requires a metric and unit, preserving the rule that incomparable provider units are not silently collapsed into one universal quantity. Aggregates expose:

- total when at least one quantity exists;
- record count;
- unavailable count;
- counts by measurement quality;
- optional time-window bounds.

A set containing only unavailable measurements therefore has `total = None`, not `0`.

## Budgets and thresholds

`UsageBudget` defines metric, unit, scope, limit, soft/hard kind, warning fraction, optional rolling window, action, owner attribution and whether estimated measurements may count. Supported canonical scopes currently include user, organization, team, project, workspace, task, run, agent, capability, model provider, Worker and Node. Budget revisions are immutable and advance monotonically; both reference stores retain version history so policy changes remain auditable.

Accounting computes `BudgetState` but does not itself deny work. Authorization/admission enforcement remains owned by #15/#14. Low-confidence estimated usage is excluded unless the budget explicitly opts in.

Crossing warning/exceeded levels emits a canonical `BudgetThresholdEvent`. The store persists the last threshold level so repeated measurements do not generate notification storms. #75 can later adapt these accounting events into user-attention notifications.

## Control Plane

`accounting_resource_services()` supplies three explicit #32 extension collections:

- `usage-records` — attributable raw records;
- `usage-aggregates` — grouped metric/unit totals with quality breakdown;
- `usage-budgets` — configured limits and current consumed/remaining state.

The services apply owner isolation before returning records, aggregates or budgets. Aggregate calculation is performed only over the already-visible record set, preventing a filtered view from accidentally summing hidden owner data. Future #15/#87 integration can add richer authorization and administrative aggregate policies without changing accounting identities.

## Deferred progressive work

The foundation deliberately does not claim completion of all #76 follow-ups. Rich Worker/Node hardware measurements, complete storage/workspace accounting, Team/Organization attribution, browser/connector/repository usage, Resources UI, notification integration, and enforcement hooks depend on their owning domains and remain progressive additions.


## Point-in-time resources and storage

Repeated accounting records now declare an aggregation mode. `additive` is used for counters and consumptive quantities such as calls, tokens and durations. `latest` is used for point-in-time state such as current storage bytes and, later, RAM/VRAM/utilization gauges. A canonical metric/unit query may not mix both modes.

`FileStorageAccounting` consumes the completed #13 `FileProvider` boundary and records `storage.file.bytes.current` in bytes. It sums only READY canonical `FileRecord.size_bytes` values visible through the provider's scoped `list_files()` call. Tombstoned or pending files are not counted. The measurement is provider-reported by default because a replaceable FileProvider may obtain size metadata differently; callers may mark it measured only when their provider contract justifies that classification.

Storage reconciliation does not infer usage ownership from `DataAccessContext.actor_ref`: the actor performing a measurement is not necessarily the owner of the measured resources. Project scope is inherited from the FileProvider request; user/team/organization ownership must be supplied explicitly when the caller actually knows it.

Provider errors may record an unavailable latest measurement but are re-raised. An unavailable gauge therefore never becomes a fabricated zero. Budget evaluation retains its last available value until a new available gauge is reported, while aggregate/current-state queries expose a latest unavailable sample as unavailable.
