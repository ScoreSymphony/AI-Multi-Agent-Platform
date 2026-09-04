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

Automatic and manual retries share this same counter and never mint a replacement
`TriggerDelivery`.

### Backoff strategy

The reference strategy is deterministic exponential backoff without jitter:

`delay = base_backoff_seconds * 2 ** (failed_attempt - 1)`

Therefore attempt-1 failure waits one base interval, attempt-2 failure waits two base intervals,
and attempt-3 failure waits four base intervals.

No maximum-backoff field or jitter is introduced in this slice. Both remain replaceable future
policy additions if operational evidence justifies them. Deterministic tests remain a hard
requirement.

`base_backoff_seconds = 0` remains valid for compatibility. The reference runtime clamps repeated
due wakeups so persistent zero-delay failures cannot become a CPU spin loop.

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

A persisted `ContractError.retryable = true` hint overrides a broad error category when an adapter
has authoritative transient-failure knowledge.

### Terminal delivery failures

Stable request, configuration, authorization and contract/capability failures are terminal for the
unchanged delivery. Delivery-level failure is not itself `AutomationState.INVALID`.

The final runtime layer may automatically invalidate after a permanent delivery failure only for
categories that directly prove durable Automation configuration incompatibility. The initial set is
`invalid_configuration` and `unsupported_capability`. Transient provider/worker/backend failure
never invalidates an Automation.

### Persistence shape

Retry state is stored on the existing durable `TriggerDelivery` JSON record:

- delivery ID and Automation ID remain unchanged;
- `attempt` remains authoritative;
- `retryable` records the current retry disposition;
- `last_failed_at` records the latest failed attempt;
- `next_retry_at` records the deterministic pending retry deadline;
- `retry_exhausted_at` records durable exhaustion.

The SQLite table schema does not need migration because the existing payload column already stores
canonical delivery JSON. Rows created before #241 remain readable with safe defaults for missing
retry fields. No backend-private scheduler/job identifier becomes canonical identity.

### Runtime wakeup behavior

`AutomationRuntime` considers both:

1. the next normal schedule evaluation; and
2. the next pending delivery retry.

A retry re-enters `AutomationService.retry_delivery()` and therefore the same TaskCreator / normal
Task-admission path. It never calls an orchestrator, executor, worker or tool directly.

### Lifecycle interaction

Pending automatic retry executes only while the owning Automation is `ENABLED`. `PAUSED`,
`DISABLED` and `INVALID` suppress automatic retry execution without resetting the persisted attempt
counter or creating another delivery.

Manual retry and automatic retry converge on the same persisted delivery. The retry path re-reads
durable state under the delivery lock, so a manual attempt that changes status/attempt while an
automatic retry is pending cannot cause a stale duplicate attempt.

### Audit outcomes

The Automation subsystem exposes structured outcomes for:

- retry scheduled;
- retry started;
- retry succeeded;
- retry exhausted;
- retry suppressed by lifecycle state.

## Workstream B — `AutomationState.INVALID`

`INVALID` is reserved for durable configuration/lifecycle invalidity, not delivery-level runtime
failure. Transient provider, worker, node or external-system unavailability does not invalidate an
Automation.

### Canonical metadata

An invalid Automation persists only categorical, non-secret metadata:

- `invalidation_reason_code`;
- `invalidated_at`;
- `state_before_invalid`.

Reason codes are deliberately restricted machine categories rather than provider error text. A
record is valid only when all three fields are present exactly while state is `INVALID`.

Rows created by #18 that already contain the `INVALID` enum but no #241 metadata remain readable.
They are conservatively loaded as `legacy_invalid_state`, with the previous state treated as
`DISABLED`, so explicit revalidation cannot unexpectedly enable old state.

### Lifecycle transitions

Generic `set_state()` cannot enter or leave `INVALID`. Entry uses explicit invalidation semantics;
exit uses explicit revalidation semantics. Both operator-triggered commands are canonical
Automation commands and require `ADMINISTER` authorization.

Invalidation preserves the current schedule position. Repeated invalidation may replace the safe
reason category but keeps the original `invalidated_at` and original `state_before_invalid`.
Successful revalidation restores exactly that prior `ENABLED`, `PAUSED` or `DISABLED` state and
clears invalidation metadata without recomputing the schedule.

### Revalidation

`AutomationService._validate_configuration_for_revalidation()` is a replaceable validation seam.
The reference implementation relies on already validated local canonical dataclasses/repository
records, while integrations may resolve durable provider/secret/reference configuration there.

- transient validation failure leaves the Automation unchanged in `INVALID` and emits
  `revalidation_deferred`;
- permanent validation failure leaves it `INVALID`, updates only the safe reason category and emits
  `revalidation_failed`;
- successful validation restores the prior lifecycle state and emits `revalidated`.

Incoming webhook deliveries are rejected before admission while invalid. Scheduled/platform-event
processing and automatic retries already require `ENABLED`, so they cannot execute while invalid.

## Workstream C — workspace-aware platform events

The platform deliberately does **not** add an Automation-only `workspace_id` to canonical Event
payloads. Current canonical `Event` carries owner/project scope, while #37 already owns the
authoritative Workspace and Run→Workspace relationships. #241 therefore uses an equivalent
provider-neutral authoritative resolver rather than duplicating workspace identity inside events.

### Authoritative resolution

`WorkspaceEventScopeResolver` is the replaceable Automation seam. The reference
`CanonicalWorkspaceEventScopeResolver` proves workspace scope from canonical #37 state:

1. for Run subjects, the durable `RunWorkspaceBindingRepository` is authoritative and survives
   materialization release/restart;
2. when a `WorkspaceProvider` is available, the resolved workspace is checked against canonical
   Event project/owner scope;
3. active Workspace task/run references provide a reference-path fallback while a materialization
   is active;
4. missing, ambiguous or inconsistent relationships resolve to no workspace and therefore fail
   closed.

The runtime composition automatically wires this resolver when canonical workspace provider and/or
Run workspace binding dependencies are configured. A custom Automation service may bind another
resolver behind the same seam.

### Visibility order and non-disclosure

Canonical event processing follows this order:

1. Automation enabled/type/event-type and historical-event eligibility;
2. project visibility;
3. workspace visibility when the Automation is workspace-scoped;
4. owner/service visibility where project scope does not already authorize the event;
5. trigger filters;
6. delivery/dedupe mutation.

A workspace-scoped Automation therefore receives an Event only when the canonical relationship
proves exactly the configured workspace. Cross-workspace, unresolvable and resolver-error cases
create no TriggerDelivery and no dedupe state.

Visibility rejection audit records contain only the Automation identity/revision, abstract reason
category and booleans indicating configured project/workspace scope. They intentionally omit the
hidden Event ID, subject ID and resolved/foreign workspace ID so rejection observability cannot
become a cross-workspace existence side channel.

## Workstream D — current-main verification

Completion requires the full #18/#241 regression surface plus the repository's then-current
backend, frontend, deterministic evaluation, LiteLLM, package/install and real Forge/Hermes gates
on combined `main`. This feature branch is only an implementation vehicle; isolated green tests are
not sufficient completion evidence.
