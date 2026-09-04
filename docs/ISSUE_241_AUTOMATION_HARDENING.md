# Issue #241 — Automation hardening decisions

This document records the implementation decisions for the follow-up hardening slice owned by
#241. It supplements `docs/AUTOMATION.md`; it does not redefine the canonical invariant from #18:

`Trigger -> Automation evaluation -> canonical Task creation -> normal platform lifecycle`

## Workstream A — durable automatic retry/backoff

### Attempt numbering

`TriggerDelivery.attempt` is authoritative. The initial processing pass is attempt `1`. A retry
increments the same persisted delivery to attempt `2`, then `3`, and so on. `max_attempts` is the
total number of processing attempts, not the number of retries after the first failure.

Examples:

- `max_attempts = 1`: initial attempt only, no retry;
- `max_attempts = 3`: initial attempt plus at most two retries.

Automatic and manual retries share this same counter and must never mint a replacement
`TriggerDelivery`.

### Backoff strategy

The initial reference strategy is deterministic exponential backoff without jitter:

`delay = base_backoff_seconds * 2 ** (failed_attempt - 1)`

Therefore attempt-1 failure waits one base interval, attempt-2 failure waits two base intervals,
and attempt-3 failure waits four base intervals.

No maximum-backoff field or jitter is introduced in this slice. Both can be added behind the
replaceable retry-policy seam later if operational evidence justifies them. Deterministic tests
remain a hard requirement.

`base_backoff_seconds = 0` remains valid for compatibility. Runtime integration must prevent a
persistent zero-delay failure from becoming a spin loop by yielding/clamping repeated wakeups.

### Retryable failures

Automatic retry is intended for operational failures where retrying the same canonical Task
admission may succeed without changing Automation configuration. The initial retryable floor is:

- `model_unavailable`;
- `unavailable`;
- `timeout`;
- `rate_limited`;
- `resource_exhausted`;
- `transient_failure`;
- `backend_error`;
- the legacy provider-neutral `automation_task_creation_failed` fallback emitted for unexpected
  `TaskCreator` exceptions.

A persisted `ContractError.retryable = true` hint must be able to override a broad error category
when the adapter has authoritative transient-failure knowledge.

### Terminal delivery failures

Stable request, configuration, authorization and contract/capability failures are terminal for the
unchanged delivery. They do not become `AutomationState.INVALID` automatically merely because one
delivery failed. Examples include:

- invalid request/configuration;
- unsupported capability;
- not found / no compatible route when reported as a stable admission result;
- input or provider-response contract violations;
- unauthorized / forbidden;
- permanent failure;
- cancelled;
- conflict without an explicit retryable hint.

Unknown future error codes fail closed as terminal unless accompanied by an explicit persisted
retryable hint.

### Required persistence shape

The runtime integration must persist enough metadata to reconstruct the retry decision after a
process restart. The planned durable record is keyed by the existing `TriggerDelivery.id` and must
include at least:

- delivery ID;
- automation ID;
- failed attempt number;
- retry disposition;
- deterministic `next_retry_at` when pending;
- exhausted/suppressed state where applicable;
- safe error category (never secret values).

No backend-private scheduler/job identifier becomes canonical identity.

### Runtime wakeup behavior

`AutomationRuntime` will consider both:

1. the next normal schedule evaluation; and
2. the next pending delivery retry.

A retry re-enters `AutomationService.retry_delivery()` and therefore the same TaskCreator / normal
Task-admission path. It must not call an orchestrator, executor, worker or tool directly.

### Lifecycle interaction

Pending automatic retry is permitted to execute only while the owning Automation is `ENABLED`.
`PAUSED`, `DISABLED` and `INVALID` suppress automatic retry execution without resetting the
persisted attempt counter or creating another delivery. Re-enabling/revalidating may make a still
eligible retry runnable again according to the persisted retry record.

Manual retry and automatic retry must converge on the same persisted delivery. If a manual retry
changes the attempt/status while an automatic retry is pending, the runtime must re-read durable
state before processing and must not perform a stale duplicate attempt.

### Audit outcomes

The Automation subsystem will expose structured outcomes for at least:

- retry scheduled;
- retry started;
- retry succeeded;
- retry exhausted;
- retry suppressed by lifecycle state.

## Workstream B — `AutomationState.INVALID`

`INVALID` is reserved for durable configuration/lifecycle invalidity, not delivery-level runtime
failure. Transient provider, worker, node or external-system unavailability must not invalidate an
Automation.

The implementation will add explicit invalidation reason/category metadata, authorization-checked
revalidation, auditable transitions and deterministic recovery to the appropriate prior operator
state without corrupting schedule metadata.

## Workstream C — workspace-aware platform events

Automation will not invent an Automation-only workspace ID inside event payloads. Workspace
visibility will be wired only through the canonical Event/workspace contract or a documented
authoritative resolver that fails closed and cannot expose cross-workspace existence.

Visibility/authorization checks remain before trigger filters and before any dedupe mutation.

## Workstream D — current-main verification

Completion requires the full #18 regression surface plus the repository's then-current backend,
frontend, LiteLLM and real Forge-sidecar gates on combined `main`. This feature branch is only an
implementation vehicle; isolated green tests are not sufficient completion evidence.
