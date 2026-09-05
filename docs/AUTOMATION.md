# Automation, Triggers and Event-Driven Task Creation

Issue #18 introduced Automation as a canonical platform domain without creating a second execution system. Issue #241 hardens that baseline with durable automatic delivery retries, an explicit recoverable `INVALID` lifecycle and fail-closed workspace-aware platform-event visibility.

## Core invariant

```text
Trigger
  -> Automation evaluation
  -> canonical Task creation
  -> normal authorization / planning / routing / execution lifecycle
```

Automations never call orchestrators, executors, tools or workers directly.

## Canonical concepts

### Automation

An Automation stores:

- canonical `automation_*` ID;
- name and description;
- owner identity and optional project/workspace scope;
- enabled, paused, disabled or invalid state;
- versioned Trigger definition;
- canonical Task template;
- deduplication, retry and overlap policy;
- created/updated timestamps and revision;
- last/next schedule evaluation metadata;
- while invalid, categorical `invalidation_reason_code`, `invalidated_at` and `state_before_invalid` metadata.

The identity is captured from the canonical actor context that creates the Automation. Automation creation does not accept an arbitrary replacement identity, so a creator cannot mint a more privileged execution identity through Automation configuration.

`automation.create` and `automation.update` expose the canonical retry and overlap policies. The baseline deduplication strategy is `delivery_key`; unsupported strategy names are rejected instead of being silently accepted.

### Trigger

The baseline supports:

- `one_time`: one timezone-aware scheduled occurrence;
- `recurring`: a timezone-aware first occurrence plus deterministic interval;
- `webhook`: verified external delivery from an expected source;
- `platform_event`: filtered canonical platform-event delivery;
- `manual`: deterministic test delivery.

The reference recurring scheduler intentionally uses a small interval-based contract rather than binding the canonical model to a specific cron library. A cron adapter can later map onto the same Trigger contract.

### TriggerDelivery

Each occurrence is persisted as a canonical `trigger_delivery_*` record containing source, fired/received timestamps, payload, dedupe key, processing status, attempts, generated Task ID and canonical failure details.

Retry state remains on that same durable delivery through `retryable`, `last_failed_at`, `next_retry_at` and `retry_exhausted_at`. The repository enforces a unique `(automation_id, dedupe_key)` occurrence, so redelivery and automatic retry cannot create a replacement TriggerDelivery accidentally, including after process restart when the SQLite repository is used.

## Scheduling semantics

All schedule timestamps are timezone-aware. Canonical comparisons and next-run calculations use UTC timestamps while the Trigger retains explicit timezone metadata.

The deterministic reference scheduler supports:

- one-time schedules;
- recurring interval schedules;
- pause/resume/disable;
- revisioned schedule edits;
- persisted next-run metadata;
- restart recovery;
- `coalesce` missed-run behavior (default);
- `skip` missed-run behavior.

`coalesce` creates at most one delivery after downtime and advances the next occurrence past the current time. `skip` advances past missed occurrences without creating work.

`AutomationRuntime` combines the next normal schedule evaluation with the next durable delivery-retry deadline when calculating its wakeup. No external broker or workflow engine is required by the reference path.

## Overlap semantics

The baseline exposes two explicit policies:

- `skip_while_processing` (default): one delivery for the Automation owns the processing lock. A distinct occurrence arriving while that delivery is processing is persisted as rejected with `overlap_skipped` and never creates a Task.
- `allow`: distinct deduplicated occurrences are allowed to enter canonical Task creation concurrently. There is no per-Automation serialization lock for this policy.

Overlap policy is separate from deduplication. A redelivery with the same dedupe key still resolves to the existing TriggerDelivery and must not create duplicate work even when `allow` is selected.

Automatic retries obey the same overlap policy. With `skip_while_processing`, a due retry encountering an already-held Automation processing lock is suppressed with `retry-suppressed-overlap`; the failed TriggerDelivery, attempt count and retry deadline remain intact so a later runtime tick can retry it. With `allow`, the due retry may re-enter Task admission while another distinct delivery for the same Automation is still processing.

## Webhook boundary

The canonical Automation service does not own provider-specific signature algorithms. The HTTP/connector verification boundary supplies the result of authenticity verification to the service.

Webhook Trigger configuration stores `verification_ref` only. Raw secrets, tokens or signing keys are rejected from the canonical Trigger payload.

Untrusted webhook input is bounded before provider-specific verification runs. The reference service enforces configurable limits for:

- canonical JSON payload bytes (default: 256 KiB);
- event ID length (default: 512 characters);
- source identifier length (default: 128 characters).

Oversized input is rejected with the canonical `input_too_large` error and does not create a TriggerDelivery or invoke Task creation. Payloads that cannot be represented as canonical JSON are rejected as `invalid_request`.

A failed authenticity check is persisted as a rejected TriggerDelivery and never reaches Task creation. Unexpected webhook sources are also persisted as rejected history instead of disappearing as untraceable admission failures.

The public Automation service additionally exposes a replaceable async payload-validation hook for provider- or connector-specific schema checks. A validator rejects by raising `ContractError`; the rejection is persisted before the error crosses the service boundary. This keeps schema technology replaceable rather than binding the canonical Trigger model to one schema engine.

Webhook task admission is also bounded by a configurable per-Automation/per-source fixed-window rate limit (default: 120 unique deliveries per 60 seconds). Duplicate deliveries with an existing dedupe key are resolved before the rate counter, so legitimate redelivery remains idempotent and cheap. The reference limiter is intentionally in-process resource protection; a distributed deployment may replace or front it with shared ingress controls without changing canonical Automation semantics.

An `INVALID` Automation rejects webhook delivery before normal admission.

## Platform-event triggers

Platform-event Triggers match one canonical `event_type` and optional exact field filters. The service consumes canonical Event semantics directly and has no broker dependency. Issue #35 may later provide distributed transport without changing the Automation contract.

Visibility is evaluated before trigger filters or delivery/dedupe mutation. Project-scoped Automations require the matching canonical project. Workspace-scoped Automations additionally require workspace scope to be proven by the replaceable `WorkspaceEventScopeResolver`.

The reference `CanonicalWorkspaceEventScopeResolver` does not add an Automation-only `workspace_id` to Event payloads. It resolves workspace identity from canonical #37 workspace state and durable Run-to-Workspace bindings. Missing, ambiguous, inconsistent or failing resolution is fail-closed. Rejection audit records contain only abstract scope reason categories and omit hidden event, subject and foreign-workspace identifiers.

Owner-only behavior remains conservative. An unowned/global event is visible only to a service-owned Automation when no project/workspace scope already authorizes it. Historical events older than the Automation are not treated as replay requests.

## Task creation and provenance

The Automation Control Plane adapter renders the configured Task template and calls the existing `ControlPlane.create_task(...)` path using the Automation identity and a deterministic idempotency key.

The generated Task therefore still passes normal `task:create` authorization and Task-management validation. Automatic retries re-enter this same path and re-check current authorization; retry state is not an authorization cache.

Tasks receive provenance labels:

- `automation:<automation_id>`
- `delivery:<trigger_delivery_id>`

The TriggerDelivery also stores the generated canonical Task ID, giving bidirectional queryable correlation without a separate Task type.

## Audit and observability

Automation creation, updates and ordinary state changes emit backend-neutral `automation.configuration` events containing the Automation ID, revision, changed fields, state and update timestamp. The events distinguish the Automation execution identity (`automation_principal_ref`) from the authenticated principal that performed the configuration mutation (`changed_by_principal_ref`). For direct service embeddings without a request actor, the Automation principal is the deterministic fallback.

Delivery processing emits `automation.delivery` events with Automation and TriggerDelivery IDs, generated Task ID, source, fired/received timestamps, attempt, duration, dedupe/outcome information, canonical error code and retry state. Retry outcomes include `retry-scheduled`, `retry-started`, `retry-succeeded`, `retry-exhausted`, lifecycle suppression and overlap suppression.

Invalidation and recovery emit `automation.lifecycle` events with categorical reason/state metadata only. Workspace/event visibility rejection uses `automation.event_visibility` with non-disclosing reason categories.

An observability implementation may enrich or export these events, but the Automation engine exposes the structured data independently of that backend.

## Retry semantics

Retryable processing failures are scheduled automatically on the same persisted TriggerDelivery. The initial processing pass is attempt `1`; manual and automatic retries increment the same counter and share `RetryPolicy.max_attempts`.

The reference backoff is deterministic exponential backoff without jitter:

```text
delay = base_backoff_seconds * 2 ** (failed_attempt - 1)
```

`RetryPolicy.base_backoff_seconds` therefore affects actual runtime scheduling. `base_backoff_seconds = 0` remains valid; the runtime applies a polling floor so repeated zero-delay failures cannot form a CPU spin loop.

`next_retry_at` and exhaustion state are durable and survive restart. `PAUSED`, `DISABLED` and `INVALID` suppress automatic retries without consuming an attempt or replacing the delivery. Re-enabling or successful revalidation allows an already-due retained retry to proceed.

Manual `automation.retry-delivery` and automatic retry converge on the same processing path. Both use the same deterministic Task-admission idempotency key derived from Automation ID and delivery dedupe key. Stable configuration/authorization/contract failures are terminal unless the owning layer explicitly classifies them as retryable.

## INVALID lifecycle and revalidation

`INVALID` represents durable configuration/lifecycle invalidity, not a transient runtime failure. Examples include incompatible configuration, a permanently invalid required reference or unsupported capability. Temporary provider/backend/worker unavailability does not invalidate an Automation.

Entry and exit are explicit lifecycle operations. Generic pause/resume/disable state mutation cannot enter or leave `INVALID`.

Invalidation preserves the current schedule position and stores only safe categorical metadata:

- `invalidation_reason_code`;
- `invalidated_at`;
- `state_before_invalid`.

Successful revalidation restores the exact pre-invalid lifecycle state (`ENABLED`, `PAUSED` or `DISABLED`) without recomputing the schedule and clears invalidation metadata. Transient validation failure leaves the Automation unchanged in `INVALID`; permanent validation failure remains invalid with a safe reason category. The configuration-validation step is a replaceable service seam for integrations that own secrets, providers or external references.

Legacy durable rows that already contain `state=invalid` but predate the metadata are read conservatively as `legacy_invalid_state` with previous state `DISABLED`, requiring explicit revalidation before they can return to service.

## Control Plane resources

The composed Control Plane registers:

- `automations`
- `automation-deliveries`

and these canonical commands:

- `automation.create`
- `automation.update`
- `automation.pause`
- `automation.resume`
- `automation.disable`
- `automation.invalidate`
- `automation.revalidate`
- `automation.test`
- `automation.webhook`
- `automation.event`
- `automation.evaluate`
- `automation.retry-delivery`

`automation.invalidate` and `automation.revalidate` are administrative Automation lifecycle commands and require the canonical Automation `ADMINISTER` authorization vocabulary.

They use the generic versioned Control Plane extension route and therefore remain independent of the frontend.

Automation composes above the current Search-enabled Control Plane layer rather than replacing it. Existing platform extensions, including Agent runtime collections registered after Control Plane construction, coexist with the canonical Automation collections; Automation route and command names are reserved against accidental extension override.

## Replaceability

`AutomationRepository` is the durable storage seam. The baseline includes in-memory and SQLite implementations.

`ReferenceScheduler` is deterministic and in-process. Normal schedule evaluation delegates back to `AutomationService`; `AutomationRuntime` adds restart-safe wakeups for both schedule and retry deadlines.

`WorkspaceEventScopeResolver` is the provider-neutral workspace visibility seam. The reference implementation resolves canonical #37 workspace relationships without changing Event payload identity.

`TaskCreator` is the admission port. The production Control Plane implementation binds this port to canonical Task creation. Test or alternate embeddings may supply another implementation, but the public composed platform never routes Automation directly to execution providers.

No Temporal installation, distributed broker, paid scheduler, frontend or connector framework is required for the reference path.
