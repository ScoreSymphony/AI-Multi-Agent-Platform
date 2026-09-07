# Issue #18 Runtime Completion

The original #18 implementation established the canonical Automation, Trigger and TriggerDelivery domain and the canonical Task-admission path. A post-merge audit found that four runtime concerns were still only present as lower-level primitives rather than as a complete reference runtime:

1. schedules required an explicit `tick()` / `automation.evaluate` call;
2. the restart-safe SQLite repository existed but was not composed into a durable reference path;
3. canonical #6 platform events were filterable but were not consumed automatically from the canonical EventRepository;
4. `automation.event` and `automation.evaluate` were ordinary collection commands even though they act across Automation ownership scopes.

The same audit also found that configuration commands other than `automation.create` did not replay the Control Plane `Idempotency-Key`, and that Automation audit events were only persisted when an external event sink happened to be configured.

A subsequent security and runtime-resilience pass added these additional invariants:

- a newly created platform-event Automation does not retroactively consume older canonical Events;
- an unscoped Automation cannot consume another owner's canonical Events;
- project scopes requested by Automation configuration are authorized explicitly rather than inferred from the caller's generic command authorization;
- an idempotent replay rechecks current authorization and therefore cannot become a durable authorization cache after permissions are revoked;
- one malformed or temporarily failing canonical Event cannot block later Events or due schedules;
- retryable Event failures remain unacknowledged for retry, while non-retryable canonical ContractErrors become audited terminal outcomes instead of poison events that are retried forever;
- persistent runtime/scheduler failures back off by at least the configured poll interval instead of entering a zero-delay retry loop.

## Runtime-complete reference path

`ai_multi_agent_platform.automation.runtime.AutomationRuntime` is the replaceable in-process reference runner.

Each runtime pass:

1. reads unseen canonical events from the configured #6 `EventRepository`;
2. passes each trusted canonical Event object to `AutomationService.deliver_canonical_platform_event()`;
3. validates canonical event time and applies creation-time, ownership/project-scope and configured trigger-filter boundaries;
4. persists the processed-event cursor after successful evaluation;
5. records transient/retryable Event failures in `AutomationRuntimeTick.failed_event_ids` and leaves their cursor unacknowledged;
6. records non-retryable ContractErrors as durable/in-memory audit events, acknowledges their cursor, and exposes them in `AutomationRuntimeTick.terminal_event_ids`;
7. continues with later Events even when an earlier Event fails;
8. evaluates due schedules through the existing replaceable `ReferenceScheduler` even when an Event failed during the same pass.

The runtime does not call an orchestrator, executor, model, tool or Worker. Generated work still enters through the existing canonical Task creator and normal Task lifecycle.

## Autonomous lifecycle

`ControlPlaneASGI` binds the Automation runtime to ASGI lifespan:

- `lifespan.startup` starts the in-process runtime loop;
- `lifespan.shutdown` stops it cleanly;
- successful passes sleep until either the event-poll interval or the next scheduled Automation wakeup;
- a failed runtime/scheduler pass waits at least the configured poll interval before retrying, so an overdue schedule cannot create a zero-delay CPU loop during a persistent backend failure.

Embeddings that do not use ASGI may call `start_automation_runtime()`, `stop_automation_runtime()` or `run_automation_runtime_once()` directly.

## Durable state

The runtime-complete `ControlPlane` accepts `automation_state_path` or the environment variable:

```text
AI_MULTI_AGENT_PLATFORM_AUTOMATION_STATE=/path/to/automation.sqlite3
```

When configured and no custom Automation service/repository is supplied, the same SQLite file stores:

- canonical Automations;
- TriggerDeliveries and delivery dedupe state;
- processed/terminal canonical Event IDs;
- Automation command replay records;
- Automation configuration/delivery/runtime-failure audit records.

This is the durable single-process reference path. The underlying repository/runtime-state seams remain replaceable for distributed deployments.

If no state path is configured, the composition remains explicitly ephemeral and uses in-memory Automation/runtime state. This keeps unit tests and intentionally transient embeddings isolated without pretending that an in-memory process is restart-safe.

## Canonical event bridge

The runtime consumes the append-only #6 `EventRepository`; it does not invent a second event model or require a broker.

Trigger filters receive the canonical Event payload enriched with these canonical fields:

- `event_id`;
- `event_type`;
- `subject_type`;
- `subject_id`;
- `correlation_id`;
- `causation_id`;
- `trace_id`;
- `project_id`;
- `owner_type` / `owner_id` when present.

The runtime applies a conservative visibility floor before user-configured filters:

- Events older than the Automation's `created_at` are ignored, so creating a subscription does not backfire historical repository state.
- A project-scoped Automation consumes only Events with the same canonical `project_id`.
- An unscoped Automation consumes only Events whose canonical `owner_ref` exactly matches the Automation owner.
- Unowned/global Events are reserved for service-owned Automations.

Project-scoped Automation configuration is itself authorization-checked at the Control Plane boundary. This is what makes same-project event consumption an explicit delegated scope rather than an owner bypass.

Pending Event ordering is tolerant of malformed/non-aware timestamps: valid canonical timestamps retain deterministic chronological ordering, while malformed timestamp values are isolated to that Event. The canonical runtime-facing service then converts a non-aware `occurred_at` into a non-retryable `CONTRACT_VIOLATION`, which is audited and terminally acknowledged rather than retried indefinitely.

Retryable `ContractError`s, including explicit retryable failures and stable transient/backend error categories, remain unacknowledged for a later pass. Unknown exceptions are also treated conservatively as retryable because the runtime must not discard a canonical Event without a stable platform error category proving terminality. If delivery/task creation succeeded but cursor persistence fails, TriggerDelivery deduplication remains the final duplicate-work guard on the next attempt.

## Internal trigger authority

`automation.event` and `automation.evaluate` remain registered for compatibility/administrative integration, but are now reserved for canonical service actors.

Normal user/team/organization actors cannot invoke them, even if a broad generic authorization provider would otherwise allow the command.

Additional restrictions:

- `automation.evaluate` cannot accept a caller-supplied `now` override;
- `automation.event` requires a canonical `event_*` ID;
- `automation.event` cannot override the canonical event timestamp.

The normal Automation runtime does not use these commands. It calls the scheduler/event service internally from trusted canonical state.

## Configuration authorization and command idempotency

The runtime-complete Control Plane persists successful replay records for:

- `automation.create`;
- `automation.update`;
- `automation.pause`;
- `automation.resume`;
- `automation.disable`.

The replay key is scoped to the authenticated principal plus `Idempotency-Key`. Exact replay returns the prior canonical resource representation without applying another revision. Reuse of the same key for a different command, resource or payload returns canonical `CONFLICT`.

Replay records do not cache permission. Before either applying a command or returning a persisted replay result, the Control Plane re-evaluates current authorization. A principal whose permission has since been revoked therefore receives `FORBIDDEN` rather than the historical result.

For `automation.create`, top-level `project_id` and `task_template.project_id` are explicitly included in the authorization context. For `automation.update`, a newly requested task-template project scope is separately authorized when it differs from the Automation's stored project scope.

With SQLite runtime state the persisted replay behavior survives process restart. The reference path does not claim a cross-process atomic transaction spanning an Automation mutation and its northbound replay record; distributed/multi-process command coordination remains behind the replaceable repository/runtime-state seams.

## Audit persistence

When the Control Plane owns the canonical AutomationService, its configuration and delivery event sink is always connected to the runtime-state store. Runtime-terminal canonical Event failures are also recorded there with event identity, subject/project metadata and stable error category before their event cursor is acknowledged. If that audit/cursor persistence fails, the Event remains retryable instead of being silently discarded.

A caller-supplied event sink is teed after runtime persistence rather than replacing it. A custom externally supplied AutomationService remains responsible for its own event-sink policy because the platform must not mutate private service internals.

## Tests

`tests/test_issue_18_runtime_completion.py` covers:

- autonomous one-time schedule execution without manual `tick()`;
- durable Automation/Delivery state across Control Plane restart;
- canonical kernel-event ingestion through the #6 EventRepository;
- persisted event cursor and no duplicate work after restart;
- durable configuration-command replay across restart;
- conflicting Idempotency-Key reuse;
- persisted Automation audit records;
- user rejection for global event/scheduler commands;
- rejection of caller-controlled scheduler time;
- ASGI lifespan start/stop of the autonomous runtime.

`tests/test_issue_18_runtime_security.py` additionally covers:

- no historical Event backfire after a new Automation subscription is created;
- no cross-owner event triggering for an unscoped Automation;
- explicit authorization of requested Automation project scope;
- current-authorization recheck on an idempotent replay after permission revocation;
- isolation of one terminal/malformed Event so a later Event still succeeds;
- terminal Event failure auditing and cursor acknowledgement;
- retryable Event failure remaining unacknowledged;
- schedule evaluation still running when Event ingestion fails;
- malformed/non-aware Event timestamps not aborting pending-Event discovery.

The earlier #18 tests remain authoritative for webhook verification, webhook/resource limits, schedule semantics, owner-scoped reads/mutations, retry behavior, overlap policy, revision races and canonical Task provenance.
