# Automation, Triggers and Event-Driven Task Creation

Issue #18 introduces Automation as a canonical platform domain without creating a second execution system.

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
- last/next schedule evaluation metadata.

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

The repository enforces a unique `(automation_id, dedupe_key)` occurrence. Redelivery therefore cannot create a second Task unintentionally, including after process restart when the SQLite repository is used.

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

## Overlap semantics

The baseline exposes two explicit policies:

- `skip_while_processing` (default): one delivery for the Automation owns the processing lock. A distinct occurrence arriving while that delivery is processing is persisted as rejected with `overlap_skipped` and never creates a Task.
- `allow`: distinct deduplicated occurrences are allowed to enter canonical Task creation concurrently. There is no per-Automation serialization lock for this policy.

Overlap policy is separate from deduplication. A redelivery with the same dedupe key still resolves to the existing TriggerDelivery and must not create duplicate work even when `allow` is selected.

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

## Platform-event triggers

Platform-event Triggers match one canonical `event_type` and optional exact field filters. The baseline service consumes canonical event semantics directly and has no broker dependency. Issue #35 may later provide distributed transport without changing the Automation contract.

## Task creation and provenance

The Automation Control Plane adapter renders the configured Task template and calls the existing `ControlPlane.create_task(...)` path using the Automation identity and a deterministic idempotency key.

The generated Task therefore still passes normal `task:create` authorization and Task-management validation. It receives provenance labels:

- `automation:<automation_id>`
- `delivery:<trigger_delivery_id>`

The TriggerDelivery also stores the generated canonical Task ID, giving bidirectional queryable correlation without a separate Task type.

## Audit and observability

Automation creation, updates and state changes emit backend-neutral `automation.configuration` events containing the Automation ID, revision, changed fields, state and update timestamp. The events distinguish the Automation execution identity (`automation_principal_ref`) from the authenticated principal that performed the configuration mutation (`changed_by_principal_ref`). For direct service embeddings without a request actor, the Automation principal is the deterministic fallback. This provides an auditable mutation trail even when no external observability backend is installed.

Delivery processing emits `automation.delivery` events with Automation and TriggerDelivery IDs, generated Task ID, source, fired/received timestamps, attempt, duration, dedupe/outcome information and canonical error code. Schedule deliveries additionally include timezone, configured start, interval, missed-run policy and current next-evaluation metadata.

An observability implementation may enrich or export these events, but the Automation engine exposes the structured data independently of that backend.

## Retry semantics

Processing failures remain on the same persisted TriggerDelivery and explicit retry reuses that Delivery and the same deterministic Task idempotency key. `RetryPolicy.max_attempts` is enforced by the canonical service, so a processing retry cannot create an unlimited series of Task-admission attempts.

`base_backoff_seconds` is retained as declarative retry-policy metadata for a future durable/automatic retry scheduler. The baseline `automation.retry-delivery` command is an explicit operator/API retry and does not sleep inside the request path. This separation avoids turning Control Plane requests into long-running timers while preserving a stable policy field for a later scheduler implementation.

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
- `automation.test`
- `automation.webhook`
- `automation.event`
- `automation.evaluate`
- `automation.retry-delivery`

They use the generic versioned Control Plane extension route and therefore remain independent of the frontend.

Automation composes above the current Search-enabled Control Plane layer rather than replacing it. Existing platform extensions, including Agent runtime collections registered after Control Plane construction, coexist with the canonical Automation collections; Automation route and command names are reserved against accidental extension override.

## Replaceability

`AutomationRepository` is the durable storage seam. The baseline includes in-memory and SQLite implementations.

`ReferenceScheduler` is deterministic and in-process. It only evaluates due Trigger occurrences and delegates them back to `AutomationService`.

`TaskCreator` is the admission port. The production Control Plane implementation binds this port to canonical Task creation. Test or alternate embeddings may supply another implementation, but the public composed platform never routes Automation directly to execution providers.

No Temporal installation, distributed broker, frontend or connector framework is required for the reference path.
