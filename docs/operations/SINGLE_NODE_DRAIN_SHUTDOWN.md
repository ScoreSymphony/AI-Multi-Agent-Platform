# Single-node graceful drain and shutdown recovery

Issue: #1152  
Recovery authority: #707  
Deployment baseline: #39

## Invariant

Graceful drain is a **process-local admission policy**, not a persisted lifecycle.

Canonical Task, Plan, Step, Run, Event, Workspace, Artifact, Approval and Verification state
continues to be owned by the normal platform subsystems. If the process exits before work settles,
the next \x60platform-server serve\x60 invocation runs the existing #707 startup reconciliation before
authoritative serving begins.

The shutdown path therefore never invents a terminal Task/Run/Step outcome simply because the
operator stopped the process.

## Operator sequence

For a normal service-manager stop or \x60Ctrl+C\x60:

1. the Uvicorn server enters the single-node drain state before listener/request teardown;
2. new authoritative HTTP mutations are rejected with retryable HTTP \x60503 unavailable\x60;
3. new terminal WebSocket sessions are rejected with close code \x601013\x60;
4. read-only inspection remains available while the process is still serving;
5. already-admitted mutations may settle only before the shared drain deadline;
6. Uvicorn connection/task settlement and ASGI lifespan teardown use the same configured bound;
7. if the deadline expires, remaining process-local teardown is cancelled and the process exits;
8. the next startup reconciles canonical durable state through #707 before becoming ready.

The default bound is 30 seconds. Configure it explicitly with:

\x60\x60\x60bash
export AI_MAP_SHUTDOWN_TIMEOUT_SECONDS="30"
\x60\x60\x60

The supported range is 1–3600 seconds.

## Health and readiness during drain

While draining:

- \x60GET /api/v1/health\x60 remains HTTP \x60200\x60 as a liveness surface;
- \x60GET /api/v1/readiness\x60 returns HTTP \x60503\x60;
- both payloads set \x60status="draining"\x60, \x60ready=false\x60, and \x60draining=true\x60;
- the \x60drain\x60 object reports the process-local active-mutation count, timeout, forced flag and
  completion/force reason.

The drain flag is intentionally absent after process restart. A new process starts in
\x60serving\x60 admission state and must pass #707 reconciliation before \x60platform-server serve\x60 opens
the normal serving path.

## In-flight disposition policy

| State at drain entry | Shutdown disposition | Restart authority |
| --- | --- | --- |
| mutation admitted but not yet dispatched | may finish only inside the shared deadline; otherwise uncommitted process work disappears and durable state is reconciled | owning canonical subsystem / #707 |
| dispatched but not acknowledged | do not guess success/failure and do not blindly redispatch during shutdown | distributed/runtime ownership reconciliation; uncertain ownership may block startup |
| running local/reference Run | allow bounded completion; otherwise preserve last durable Run state | \x60PlatformKernel.recover_all()\x60 |
| running remote Worker execution | do not cancel merely because the Control Plane drains; Worker/job evidence remains authoritative | \x60DistributedRuntime.reconcile()\x60 before kernel recovery |
| retry/backoff | do not create a shutdown-specific retry loop | owning retry/coordinator runtime after restart |
| waiting dependency | preserve wait/dependency state | \x60DurablePlanStepCoordinator.reconcile_all()\x60 |
| waiting Approval | preserve exact Approval identity/binding; never auto-approve/deny on shutdown | Approval owner plus normal startup composition |
| waiting Verification | preserve exact Verification identity/binding; never invent a verdict | Verification/reviewer startup reconciliation |
| Automation creation/delivery in progress | current admitted work may finish inside the deadline; unfinished durable cursor/delivery evidence remains retryable | Automation runtime durable state on restart |
| callback/result persistence in progress | allow the existing transaction/persistence boundary to settle within the deadline; never synthesize a result | canonical repository/kernel reconciliation |
| cancellation already admitted before drain | may finish inside the deadline using the ordinary cancellation contract | canonical lifecycle recovery if interrupted |
| cancellation requested after drain begins | rejected as a new mutation; operator shutdown itself does not imply cancellation | startup recovery determines unfinished work |

The policy intentionally distinguishes **process teardown** from **canonical terminalization**.

## Bounded teardown

The same drain deadline covers the production HTTP server's graceful connection wait and the
remaining ASGI lifespan teardown. The latter includes the current autonomous Automation and
Notification runtime owners and any teardown added beneath the composed ASGI lifespan.

If teardown does not complete before the remaining deadline:

- the lifespan task is cancelled;
- a forced-drain telemetry event is recorded;
- the ASGI shutdown is allowed to complete so process exit cannot hang indefinitely;
- no canonical Task/Run/Step state is rewritten merely to make shutdown look clean.

Provider/browser/tool/transport resources that are process-private may therefore be abandoned by
a forced process exit. That is deliberate containment: their cleanup is useful but cannot become
a correctness prerequisite or a second lifecycle authority.

## Observability

The single-node drain projects structured #16 telemetry/timeline evidence:

- \x60platform.single_node.drain.requested\x60
- \x60platform.single_node.drain.entered\x60
- \x60platform.single_node.drain.timeout\x60
- \x60platform.single_node.drain.teardown_failed\x60
- \x60platform.single_node.drain.completed\x60
- metric \x60platform.single_node.drain.in_flight\x60 with a disposition attribute
- \x60platform.single_node.restart.reconciliation\x60 after the #707 startup pass

The restart event records readiness, unresolved Run count, blocked Verification count,
Plan reconciliation count, distributed-job reconciliation count and the resulting disposition.

Observability remains derived state: exporter failure cannot become lifecycle authority.

## Forced-stop recovery

After a forced timeout or hard kill, restart with the **same** \x60AI_MAP_DATA_DIR\x60:

\x60\x60\x60bash
platform-server recover-startup
platform-server serve
\x60\x60\x60

\x60serve\x60 runs the same recovery automatically. Use the explicit \x60recover-startup\x60 command first
when you want to inspect the recovery result without opening the HTTP listener.

If recovery reports an orphaned running Run, follow the existing #707 runbook in
\x60SINGLE_NODE_RELIABILITY.md\x60. Do not manually edit SQLite or the startup report.

## What drain does not do

Drain does not:

- create a durable \x60draining\x60 Task/Run/Step status;
- mark running work failed merely because the process is stopping;
- cancel every remote Worker job;
- resolve Approval or Verification state;
- retry uncertain external side effects during shutdown;
- replace backup/restore recovery;
- provide Control Plane HA or fencing.

Those boundaries are what make forced shutdown safe: durable truth remains in the canonical
owners, and startup reconciliation remains the only ordinary restart authority.
