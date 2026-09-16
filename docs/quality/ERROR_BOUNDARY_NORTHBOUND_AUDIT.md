# #983 Northbound / Platform Boundary Audit

This document records the **Observability / Telemetry / Control Plane / API / ASGI /
Lifecycle / Notifications / HA** slice of issue #983. The slice was originally audited
from `main` commit `b54708094062730424a8621bbd85e0906189bb19` on 2026-09-16 and was
cleanly reconstructed and revalidated on `main` commit
`adfab2d43b94931e5066a7c16d0752d3b6d5fe9b` after the parallel #983 slices landed.
No merge of `main` into the feature branch was used.

The repository-wide inventory, generic BaseException/cancellation/settlement audit,
persistence/repository/async-offload/portability work, provider/network adapters and
execution/tool/worker domains are owned by other #983 slices and are not repeated here.
Issue #983 deliberately remains open after this slice.

The cancellation/settlement slice landed as #1146 before this clean reconstruction.
Its stronger `control_plane/conversation_streaming.py` settlement semantics are therefore
consumed from current `main`; this slice does not duplicate or overwrite that work.

## Northbound contract

The canonical taxonomy remains `ContractError` / `ErrorCode`. No API-specific error
hierarchy was added. The public Control Plane boundary now follows this sequence:

```text
internal operation
  -> existing ContractError / ErrorCode when typed
  -> current composed ControlPlaneHTTP
  -> outer northbound containment
  -> existing APIException/APIError serialization
```

`ContractError` keeps the existing HTTP status, `ErrorCode`, API category,
`retryable` bit and structured `details`. Its Python cause is never serialized.
An unknown ordinary `Exception` that reaches the real public boundary is translated
through the existing `ErrorCode.BACKEND_ERROR` contract (HTTP 502, category
`backend`, non-retryable) with the stable message `internal platform operation failed`
and only `exception_type` in public details.

`asyncio.CancelledError`, `KeyboardInterrupt`, `SystemExit` and other process-control
`BaseException` values are not converted to API failures.

The outer boundary establishes/preserves `request_id` and `correlation_id` before
translation. Unexpected failures are logged once at that ownership boundary using
only boundary name, exception type and those identifiers; raw exception text,
tracebacks, provider payloads and causes are intentionally excluded from that log.

## Productive broad-catch inventory and decision

| Area / productive site | Classification | Decision | Rationale / resulting semantics |
| --- | --- | --- | --- |
| `control_plane/release_api.py` public HTTP catch | true outer boundary / API translation | **TRANSLATE** | Ordinary unknown exceptions are contained as canonical `BACKEND_ERROR`; typed API/contract errors retain their contract; process-control signals propagate. |
| `control_plane/release_api.py` public ASGI catch | true outer boundary / ASGI translation | **TRANSLATE** | Same canonical contract before response start; SSE receives canonical `platform.error`; a failed ASGI `send` is re-raised rather than recursively answered; disconnect is not turned into 5xx. |
| `control_plane/automation_runtime_composition.py` startup/shutdown catches | lifecycle/shutdown boundary | **KEEP + JUSTIFY** | ASGI lifespan must report ordinary runtime failure, but now emits only operation + exception type instead of `str(exc)`; cancellation/process control is not serialized. |
| `control_plane/notifications_live.py` composite startup catch | lifecycle / cleanup | **KEEP + JUSTIFY** | A partially started Automation runtime is rolled back before an ordinary startup failure is reported or a process-control failure is re-raised. |
| `control_plane/notifications_live.py` startup rollback catch | cleanup/settlement | **KEEP + JUSTIFY** | Rollback failure is secondary; for ordinary startup failure it is reported by type and cannot replace the primary failure. |
| `control_plane/notifications_live.py` two shutdown catches | lifecycle / cleanup | **KEEP + JUSTIFY** | Both independent runtimes are given a stop attempt. Ordinary failures are aggregated as secret-safe lifespan diagnostics; process-control failure is re-raised after settlement. |
| `control_plane/notifications_composition.py` bulk-update failure projection | notification/secondary path | **KEEP + JUSTIFY** | Canonical management command remains authoritative; post-command attention projection is attempted and the original command error is re-raised. |
| `control_plane/notifications_composition.py` bulk-read / task-change projection catches | notification/secondary path | **KEEP + JUSTIFY** | Notification attention is derived state and must not turn an already committed Task change into a false failure. Cancellation is not caught. |
| `control_plane/notifications_source_composition.py` source attention, approval, budget, connector and automation projection catches | notification/secondary path | **KEEP + JUSTIFY** | These projections occur after canonical source ownership/state. They are deliberately best-effort/retryable and catch only `Exception`; source lifecycle truth remains authoritative. Persisted budget recovery marks incomplete recovery for retry rather than claiming success. |
| `notifications/events.py` projection catch | notification/secondary path | **KEEP + JUSTIFY** | Canonical event publication has already succeeded. Projection failure remains secondary. |
| `notifications/events.py` projection-failure sink catch | telemetry/observability best effort | **KEEP + JUSTIFY** | Containment prevents a failing diagnostics sink from turning a successful canonical publish into failure. Cancellation/process-control values are still not swallowed. |
| `notifications/runtime.py` per-event projection catch | notification/secondary runtime | **KEEP + JUSTIFY** | Failed event is left uncheckpointed and retryable; later independent events still project; first error is retained for diagnostics. |
| `notifications/runtime.py` reminder catch | notification/secondary runtime | **KEEP + JUSTIFY** | Reminder projection is independent derived attention; tick records failure without corrupting canonical state. |
| `notifications/runtime.py` supervisory loop catch | lifecycle/background boundary | **KEEP + JUSTIFY** | Keeps the optional projection runtime alive while storing `last_error`; cancellation is outside `Exception` and stops the task normally. |
| `notifications/delivery.py` external channel catch | notification provider boundary | **TRANSLATE** | Unknown channel exception becomes provider-neutral `RETRYABLE_FAILURE` with only `error_type`; raw provider message/payload is not persisted. |
| `notifications/service.py` configured external delivery catch | notification/secondary path | **KEEP + JUSTIFY** | In-app canonical Notification has already been persisted. External delivery failure emits only `error_type` and does not roll back canonical attention state. |
| `observability/integrations.py::AccountingBridgeExporter.emit_metric` | telemetry best effort | **KEEP + JUSTIFY** | Measurement-sink failure records only the exception type and is swallowed unless the existing explicit `strict` mode is selected. |
| `observability/hierarchy.py` operation catch | observability re-raise boundary | **KEEP + JUSTIFY** | Classifies the original ordinary failure and re-raises it. Exporter failure masking is fixed centrally at `Telemetry`, not by changing operation ownership here. |
| `observability/instrumentation.py` observed executor/model/tool operation catches | observability re-raise boundaries | **KEEP + JUSTIFY** | These catches classify and re-raise provider/execution failure; the fail-open telemetry seam prevents telemetry from replacing the primary failure. Provider/model root causes remain owned by their separate #983 slice. |
| `observability/model_provider.py` streaming/provider catches | observability re-raise boundaries | **KEEP + JUSTIFY** | Preserve original provider/cancellation behavior while recording failure classification; exporter failure is now secondary by default. |
| `high_availability/service.py` promotion reconciliation catch | HA fail-closed translation | **TRANSLATE / KEEP + JUSTIFY** | Promotion must contain an ordinary reconciler failure, release leadership best-effort, fence the instance and raise `PromotionReconciliationError` with the cause retained internally. It does not catch process control. |
| `high_availability/telemetry.py::_best_effort` | telemetry best effort | **KEEP + JUSTIFY** | Telemetry is explicitly forbidden from participating in leadership correctness; exporter failure is swallowed and no process signal is caught. |
| `high_availability/integrations.py` autonomous Automation loop catch | lifecycle/background boundary | **KEEP + JUSTIFY** | Stores the ordinary tick/authority error and keeps the optional loop alive; cancellation propagates. |
| `high_availability/integrations.py` pre-dispatch authority catch | cleanup/settlement | **KEEP + JUSTIFY** | If authority validation fails after reservation, reservation is released/persisted and the original failure is re-raised. |

### Consumed cancellation result from current main

`control_plane/conversation_streaming.py::_pump_task_events` was part of the initial
northbound audit because it contained a broad `BaseException` path. The parallel
cancellation/settlement slice #1146 landed first and now owns the productive fix on
`main`: child cancellation is explicit, ordinary child failures use `Exception`, and
pump teardown is cancellation-resistant. This PR intentionally carries **no diff** for
that file and relies on the already-merged result instead of replaying an older,
weaker implementation.

### Explicitly not owned here

- The cancellation/shutdown/settlement implementation is owned by merged PR #1146;
  this slice only verifies compatibility with its current-main result.
- Persistence/repository/async-offload/portability implementation is owned by merged
  PR #1147; this slice does not modify those paths.
- Postgres/SQLite coordination persistence implementation details are not refactored here;
  this slice owns only the HA/platform boundary semantics around them.
- Provider/model/network SDK exceptions are only **contained northbound** here. Their
  originating translation/refactor remains Chat 3 ownership.
- Execution/tool/worker failures are only **contained northbound** here. Their root catches
  remain Chat 4 ownership.
- Repository-wide scanner policy, ratchet and final broad-catch acceptance remain Chat 7.

## Observability decision

`Telemetry` is derived state, so exporter failure is now fail-open by default. Every
log/metric/span/timeline emission is contained at the exporter seam and records
`last_export_error` as the exception type only. This fixes the previous failure mode
where `TraceHierarchy` could catch a primary operation error, attempt to instrument it,
and then accidentally replace that primary error with an exporter failure before the
original `raise` executed.

An explicitly strict exporter remains possible with `strict_exporter_errors=True`.
For compatibility, exporters that already expose an explicit `strict=True` flag retain
that opt-in behavior when the Telemetry override is omitted.

Consequences:

- a successful Task/Run/provider operation is not failed by default telemetry outage;
- an ordinary primary exception remains the exception observed by the caller;
- cancellation remains cancellation even if instrumentation is broken;
- exporter failures are diagnosable by type without recursively logging their raw payload.

## Secret / diagnostic review

The slice removes raw `str(exc)` from Automation and Notification ASGI lifespan failure
messages. Unknown public HTTP/ASGI errors never serialize raw implementation exception
text or `__cause__`; public details contain only the implementation type. The new
unexpected-boundary log likewise omits exception text and traceback.

Existing typed `ContractError.details` remain part of the canonical API contract and are
therefore preserved rather than silently rewritten by the HTTP layer. The ownership rule
remains that components constructing typed public details must only place reviewed,
provider-neutral, secret-safe values there.

Notification external delivery already persists only redacted delivery metadata and, on
an unknown channel exception, records only `error_type`.

## Cancellation / disconnect / lifecycle results

- Public HTTP/ASGI catches are `Exception`, not `BaseException`.
- ASGI `http.disconnect` during request receive is treated as disconnect, not an internal
  5xx response.
- A failed ASGI `send` is not followed by a second attempted error response.
- Current `main` already carries the stronger Conversation stream cancellation/settlement
  behavior from #1146; this slice preserves it unchanged.
- Notification composite startup rolls Automation back after Notification cancellation and
  then re-raises cancellation.
- Notification composite shutdown attempts both Notification and Automation settlement,
  then re-raises process-control failure rather than serializing it.
- Automation-only lifespan reports ordinary failure safely and lets process-control signals
  propagate.
- HA leadership/fencing catches continue to catch only ordinary exceptions where they must
  translate/fail closed.

## Tests added

`tests/integration/control_plane/test_northbound_error_boundaries.py` covers:

- typed `ContractError` -> existing status/code/category/retryability/details contract;
- cause/secret non-disclosure;
- unknown exception -> canonical secret-safe `BACKEND_ERROR`;
- public HTTP cancellation and `SystemExit` propagation;
- ASGI disconnect without false 5xx;
- Notification composite startup rollback and safe lifespan diagnostics;
- startup cancellation rollback + propagation;
- shutdown cancellation with all independent cleanup attempted;
- Notification projection failure reporting remaining secondary;
- Notification projection cancellation propagation;
- telemetry exporter failure not replacing a primary typed error;
- telemetry exporter failure not replacing cancellation;
- explicit strict telemetry exporter behavior remaining available.

Conversation-stream cancellation regression coverage is supplied by the already-merged
#1146 tests and is intentionally not duplicated in this slice.

## Handoff for final #983 acceptance

Chat 7 should consume this audit together with the other slices and run the final global
scanner/CI ratchet. In particular, it should verify that later parallel work did not add a
second northbound serializer, reintroduce raw exception strings into public envelopes, or
add unreviewed `BaseException` catches around API/lifecycle paths.

This slice intentionally does **not** close #983.
