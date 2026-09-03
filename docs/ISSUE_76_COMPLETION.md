# Issue #76 completion

Issue #76 owns the canonical, provider-neutral usage/resource accounting layer. Measurement-producing domains remain replaceable and can add richer signals later without changing the accounting contract.

## Completed surface

- Durable immutable `UsageRecord` data with canonical IDs, explicit units, attribution scopes, provenance, correlation/causation and measured/reported/estimated/unavailable quality.
- Additive and point-in-time (`latest`) aggregation with missing data preserved as unavailable rather than fabricated zero.
- Task, Run, Executor, model and Capability/Tool accounting from the observability measurement seam.
- Canonical selected `ModelConfiguration` plus provider attribution for model calls, duration and provider-reported usage.
- File storage accounting using current READY bytes.
- Worker dispatch accounting and Worker/Node provider-reported resource gauges. Untyped provider numeric metadata stays namespaced as `*.provider_reported.*` in its reported unit; #76 does not relabel it as CPU, RAM, GPU or VRAM.
- Gauge-safe Control Plane aggregation: distinct Worker/Node/resource scopes are not collapsed into one arbitrary latest sample.
- Explicit external monetary records through `record_external_cost`. The ISO currency is also the aggregation unit, so unlike currencies never combine. Cost is never inferred from arbitrary provider units or free-form model metadata.
- Budgets for user, organization, team, project, workspace, task, run, agent, capability, canonical model configuration, model provider, worker and node scopes.
- Lifetime and rolling-window budget state, warning/exceeded threshold events, persisted threshold deduplication and budget version history. Estimated usage is excluded unless policy explicitly opts in.
- Read-only Control Plane resources for records, aggregates/trends and budget state. These routes pass through the canonical #15 authorization gate and then apply owner isolation.
- `/usage` UI for current records, scope-aware aggregates, historical trends, source quality, costs and budget state.

## Dependency boundaries

The accounting contract is complete without fabricating unfinished domain data. Open source-domain issues can enrich it later:

- #14 may emit typed CPU/RAM/GPU/VRAM/capacity metrics. Until then generic numeric Worker/Node metadata remains explicitly provider-reported.
- #33 may supply authoritative executed Agent/AgentTeam identity. #76 does not infer execution from planning assignments.
- #44/#74/#82 may emit Connector/Browser/Repository operation measurements through the existing capability/telemetry seams.
- #75 may consume canonical `BudgetThresholdEvent` values for notifications. Accounting owns event state/deduplication, not notification delivery.
- #87 may add membership/role-based Team/Organization visibility. #76 already enforces exact owner/organization isolation and delegates authorization to #15; it does not invent memberships.

These are progressive measurement producers or consumers, not reasons to keep the accounting contract itself open.

## Acceptance / required-test mapping

- Task/Run/duration/retry, quality, missing metrics, thresholds, restart persistence and budget history: `tests/test_issue76_accounting.py`.
- Current storage and latest-gauge semantics: `tests/test_issue76_storage_accounting.py`.
- Real Control Plane HTTP/OpenAPI resources: `tests/test_issue76_control_plane_http.py`.
- Bounded historical trends without synthetic zero/carry-forward: `tests/test_issue76_trends.py`.
- Canonical model configuration attribution under automatic routing: `tests/test_issue76_model_attribution.py`.
- Worker/Node resource ingestion, Worker dispatch, provider replacement semantics, external cost, model-config budgets, rolling/lifetime windows, gauge scope isolation, organization isolation and #15 authorization composition: `tests/test_issue76_completion.py`.

Provider replacement changes provider/provenance attribution, not the canonical Task/Run/ModelConfiguration/Worker/Node scope semantics. Unknown or unavailable measurements remain absent/unavailable and are never guessed.
