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

Mappings cover Task/Run/Executor counts, outcomes, durations, queue wait and retry measurements, reliable model/tool measurements, and the canonical #14 Node/Worker gauges described below. Model token usage is separated into input, output, total, cached and reasoning semantics so those values are never silently added together. Provider-reported usage remains `reported` and keeps provider/config provenance.

Metric ingestion uses a deterministic usage ID derived from the source `MetricRecord`, making exact repeated delivery idempotent.

## Storage and replacement

`UsageStore` is the canonical persistence boundary. The repository includes:

- `InMemoryUsageStore` for tests/composition;
- `SQLiteUsageStore` as a dependency-free durable reference implementation.

Replacing the storage provider does not change `UsageRecord`, IDs, units, quality semantics, queries or budget semantics.

## Aggregation

`AccountingService.aggregate()` requires a metric and unit, preserving the rule that incomparable provider units are not silently collapsed into one universal quantity. Aggregates expose total, record count, unavailable count, counts by measurement quality and optional time-window bounds.

A set containing only unavailable measurements therefore has `total = None`, not `0`.

Repeated accounting records declare an aggregation mode. `additive` is used for counters and consumptive quantities such as calls, tokens and durations. `latest` is used for point-in-time state such as current storage, capacity, availability and load gauges. A canonical metric/unit query may not mix both modes.

A broad `latest` query keeps one current sample per exact canonical `UsageScope` before summing. This matters for multi-Node and multi-Worker views: the newest sample from one Worker must not erase the current sample from every other Worker.

## Budgets and threshold episodes

`UsageBudget` defines metric, unit, scope, limit, soft/hard kind, warning fraction, optional rolling window, action, owner attribution and whether estimated measurements may count. Supported canonical scopes include user, organization, team, project, workspace, task, run, agent, capability, model provider, Worker and Node. Budget revisions are immutable and advance monotonically; both reference stores retain version history so policy changes remain auditable.

Accounting computes `BudgetState` but does not itself deny work. Authorization/admission enforcement remains owned by #15/#14. Low-confidence estimated usage is excluded unless the budget explicitly opts in.

Crossing warning/exceeded levels emits a canonical `BudgetThresholdEvent`. The store persists both the current threshold level and a monotonically increasing threshold generation. Warning→exceeded remains one episode; falling below threshold ends the active episode without deleting its generation; a later fresh crossing advances the generation. #75 uses that generation in notification aggregation identity, so restart recovery reconstructs the same attention while a legitimate later re-cross can create new attention. Archived/dismissed historical notifications therefore remain dedupe evidence for their episode only.

## #14 Node/Worker resource semantics

The distributed runtime exposes explicit point-in-time resource facts through #16 telemetry, which #76 normalizes as `reported` + `latest` usage records:

- `platform.node.cpu_cores_total` → `node.cpu.cores.capacity`;
- `platform.node.cpu_cores_available` → `node.cpu.cores.available`;
- `platform.node.ram_total_bytes` → `node.memory.bytes.capacity`;
- `platform.node.ram_available_bytes` → `node.memory.bytes.available`;
- `platform.node.storage_total_bytes` → `node.storage.bytes.capacity`;
- `platform.node.storage_available_bytes` → `node.storage.bytes.available`;
- `platform.node.accelerator_memory_total_bytes` → `node.accelerator.memory.bytes.capacity`;
- `platform.node.accelerator_memory_available_total_bytes` → `node.accelerator.memory.bytes.available`;
- `platform.worker.active_jobs` → `worker.jobs.active`;
- `platform.worker.concurrency_limit` → `worker.jobs.capacity`.

The pre-existing `platform.node.accelerator_memory_available_bytes` metric remains a scheduler placement fact representing the maximum on one accelerator and is not normalized as total available VRAM. Scheduler reservation metrics likewise remain reservation facts and are not treated as consumed usage. CPU/GPU time and utilization are not fabricated when #14 does not supply authoritative measurements.

Canonical `node_id` and `worker_id` come from `TelemetryContext`, so provider/backend replacement does not redefine resource identity.

## Workspace and physical storage semantics

`FileStorageAccounting` consumes the completed #13 `FileProvider` boundary and records project-level physical `storage.file.bytes.current`. It sums only READY canonical `FileRecord.size_bytes` values visible through the provider's scoped `list_files()` call. Tombstoned or pending files are not counted.

Physical FileProvider storage refuses Workspace attribution. A single canonical File can be referenced by several Workspace snapshots, so assigning those same physical bytes to every Workspace would double-count storage.

`WorkspaceSnapshotAccounting` therefore exposes separate logical current-state gauges:

- `workspace.snapshot.file_references.current` — number of path references in the canonical snapshot, `measured`;
- `workspace.snapshot.logical_bytes.current` — sum of canonical File sizes per snapshot path reference, `reported`.

Logical bytes intentionally count duplicate references because they describe snapshot footprint, not physical deduplicated storage. Provenance records the snapshot ID/revision/checksum, reference count, unique File count and the fact that physical storage is not counted there. Archive retains the last logical footprint; canonical Workspace deletion retires the current logical gauges to zero.

Storage reconciliation does not infer usage ownership from `DataAccessContext.actor_ref`: the actor performing a measurement is not necessarily the owner of the measured resources. Project scope is inherited from the FileProvider request; user/team/organization ownership must be supplied explicitly when it is canonical.

Provider errors may record an unavailable latest measurement but are re-raised. An unavailable gauge never becomes a fabricated zero.

## #33 Agent/Team attribution

`AgentRunUsageAttributor` enriches already-attributed runtime usage with exact executed revision provenance from canonical #33 `AgentRunRecord` state.

It requires both canonical `run_id` and `agent_id` to already be present in the usage scope. Team revision is added only when `team_id` is also already present. If no exact canonical run matches, or more than one match would be possible, the record is left unchanged. Planning assignments, UI selections and guessed team membership never become accounting identity.

The UsageScope remains the runtime-supplied identity; provenance may add `agent_run_id`, `agent_revision`, `team_revision` and `orchestrator_adapter_id`.

## #87 Organization/Team visibility

The base `accounting_resource_services()` keeps exact owner isolation. `organizations.accounting` adds an optional membership-aware read composition for deployments that configure #87 Organization/Team semantics.

Personal raw usage remains exact-owner isolated. Cross-member Organization or Team aggregate visibility requires either canonical Organization owner/administrator status or an active Membership carrying the explicit `accounting.aggregate.read` policy reference. Suspended, revoked or left Memberships no longer grant future aggregate visibility. Historical usage provenance is never rewritten when membership changes.

The membership-aware layer narrows visibility only; #15 remains the request authorization gate. It does not grant a request that #15 denied and it does not invent cross-Organization access.

## Control Plane

`accounting_resource_services()` supplies three explicit #32 extension collections:

- `usage-records` — attributable raw records;
- `usage-aggregates` — grouped metric/unit totals with quality breakdown;
- `usage-budgets` — configured limits and current consumed/remaining state.

Workspace, Node/Worker and executed Agent/Team records use the same canonical collections and query model; #171 does not introduce a second accounting API or persistence model.
