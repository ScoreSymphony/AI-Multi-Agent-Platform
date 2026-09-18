# Dependency degradation, bounded retries and readiness

Issue: #1156

## Purpose

This policy extends the closed #707 single-node recovery work without reopening its lifecycle
ownership. Provider/dependency failures remain subordinate to canonical platform state and are
projected through the existing #16 observability and #39 readiness boundaries.

## Failure ownership

Provider SDK exceptions do not become a second platform error model. Provider boundaries continue
to normalize failures through `ContractError` and the existing `ErrorCode` vocabulary:

- `unavailable` / `transient_failure` for temporary dependency loss;
- `timeout` for bounded deadline expiry;
- `rate_limited` / `resource_exhausted` for capacity pressure;
- `contract_violation` / `invalid_provider_response` for protocol/contract failure;
- `unauthorized`, `forbidden` and `invalid_configuration` for auth/policy/configuration;
- `backend_error` / `permanent_failure` for provider-internal terminal failure.

The health layer records the canonical code as diagnostic evidence. It does not invent provider-
specific lifecycle states.

## Bounded health probes

Each `ProviderHealthDependency` has explicit:

- `timeout_seconds`;
- `max_retries`, limited to 0..5;
- `backoff_seconds`.

Health is a safe diagnostic read, so retryable unavailability/timeouts may be retried inside those
bounds. A provider that remains unavailable after the configured attempts is projected as
`unavailable`. Caller cancellation is re-raised immediately and therefore always wins over
retries/backoff.

The health layer never retries canonical Task/Run mutations. Runtime retry ownership remains with
the existing lifecycle/coordinator mechanisms and their persisted idempotency/reconciliation rules.

## Readiness states

The backend-neutral states are:

| State | Authoritative readiness | Meaning |
| --- | --- | --- |
| `ready` | yes | required dependencies are ready |
| `degraded` | yes | optional capability/dependency is impaired |
| `reconciling` | no | canonical recovery/reconciliation is still in progress |
| `unavailable` | no | required dependency is unavailable |
| `operator_intervention_required` | no | recovery cannot safely infer an outcome |
| `draining` | no | process is alive but intentionally not accepting new work |

Process liveness remains separate. A required dependency failure therefore fails authoritative
readiness closed, while an optional failure degrades only the affected capability when unrelated
canonical operations remain safe.

The existing ordinary startup gate still completes reconciliation before `platform-server serve`
opens HTTP. The richer readiness vocabulary also covers embedded/runtime compositions and prevents
future serving modes from treating reconciliation or an operator-required state as ready.

## Recovery and flap evidence

`AggregatedHealthProvider` retains provider-neutral transition counters per dependency:

- `failure_count` increments when a dependency enters an impaired state;
- `recovery_count` increments when it returns to `ready`;
- `attempts` / `retry_count` report the bounded work used by the current probe;
- `last_retry_error_code` records the canonical reason for the most recent retry;
- `probe_duration_seconds` records current probe cost;
- `degraded_duration_seconds` records the active or just-recovered degradation duration.

These counters are diagnostics, not canonical lifecycle state. A dependency returning never creates
or retries canonical work by itself, so repeated health flap/recovery cycles cannot duplicate
Task/Run effects or widen routing/authorization policy.

## Observability events

The single-node health aggregator is wired to the existing #16 `Telemetry` facade. It emits
provider-neutral timeline/metric evidence for:

- each bounded retry decision, including canonical error code and attempt bound;
- health-probe terminal disposition and probe duration;
- dependency degradation start and recovery, including recovery duration and flap counters;
- authoritative readiness-state transitions;
- startup/runtime operational-state changes such as reconciling and operator intervention required.

Only safe identifiers and state/counter metadata are emitted; provider exception messages are not
exported. The default Telemetry boundary is fail-open, so exporter failure remains derived-state
failure and cannot make canonical Task/Run work fail.

## Operator diagnostics

`platform doctor` consumes the same canonical health/readiness payload. It reports:

- required blockers as `blocking`;
- optional degradation as `degraded`;
- backend-neutral dependency name/state/error code;
- probe attempts/retries, canonical retry reason, probe duration and degradation duration;
- failure/recovery transition counts;
- non-destructive guidance.

The guidance never recommends direct database/event-store edits. Operator-required recovery remains
bound to the supported #707/#40 commands that operate through canonical owners.

## Existing domain evidence reused by #1156

The cross-cutting policy deliberately reuses rather than duplicates existing subsystem tests:

- deterministic model unavailable/cancellation and tool unavailable/timeout/cancellation plus a
  healthy recovery phase: `benchmarking/provider_faults.py` and provider-fault CI;
- executor timeout/cancellation/retry ownership: canonical execution conformance;
- Worker loss/rejoin and startup blockers: distributed runtime/recovery integration tests;
- message reconnect/backpressure/cancellation: #35 transport tests;
- browser and connector failures: their canonical Capability/Connector boundaries;
- no-op/exporter failure isolation: #16 observability tests;
- Registry/Search/Memory/Knowledge optional capability failures: existing provider-neutral
  unavailable paths and reference-provider tests;
- persistence/startup incompatibility: #39/#40/#707 fail-closed recovery tests.

#1156 adds the missing shared health-probe/readiness/doctor regression coverage on top of those
domain-owned tests.
