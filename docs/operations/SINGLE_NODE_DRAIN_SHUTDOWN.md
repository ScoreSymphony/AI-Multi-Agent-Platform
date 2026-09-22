# Single-node graceful drain and shutdown recovery

Recovery authority: canonical startup reconciliation
Deployment baseline: canonical single-server deployment
## Invariant

Graceful drain is a **process-local admission policy**, not a persisted lifecycle.

Canonical Task, Plan, Step, Run, Event, Workspace, Artifact, Approval and Verification state
continues to be owned by the normal platform subsystems. If the process exits before work settles,
the next `platform-server serve` invocation runs the existing startup reconciliation before
authoritative serving begins.

The shutdown path therefore never invents a terminal Task/Run/Step outcome simply because the
operator stopped the process.

## Operator sequence

For a normal service-manager stop or `Ctrl+C`:

1. the Uvicorn server enters the single-node drain state before listener/request teardown;
2. the autonomous Automation and Notification loops are quiesced so they cannot begin another tick;
3. new authoritative HTTP mutations are rejected with retryable HTTP `503 unavailable`;
4. new terminal WebSocket sessions are rejected with close code `1013`;
5. read-only inspection remains available while the process is still serving;
6. already-admitted mutations may settle only before the shared drain deadline;
7. Uvicorn connection/task settlement and ASGI lifespan teardown use the same configured bound;
8. if the deadline expires, remaining process-local teardown is cancelled and the process exits;
9. the next startup reconciles canonical durable state through canonical startup reconciliation before becoming ready.

The default bound is 30 seconds. Configure it explicitly with:

```bash
export AI_MAP_SHUTDOWN_TIMEOUT_SECONDS="30"
```

The supported range is 1–3600 seconds.

For the optional Docker Compose deployment profiles, the container hard-stop grace is 3610
seconds: ten seconds beyond the maximum supported application drain budget. Compose therefore
does not shorten any supported `AI_MAP_SHUTDOWN_TIMEOUT_SECONDS` value; the application timeout
remains the canonical drain authority and normally terminates the process before the outer
container deadline.

A first SIGTERM/SIGINT requests the normal bounded drain. A second termination signal while the
server is already exiting escalates to Uvicorn force-exit semantics: connection/task settlement and
application lifespan cleanup may be skipped. The drain is then recorded as forced with
`force_reason="operator_force_signal"`; canonical Task/Run/Step/Approval/Verification state is
still not rewritten. The next startup must reconcile the same durable data root through canonical startup reconciliation.

## Health and readiness during drain

While draining:

- `GET /api/v1/health` remains HTTP `200` as a liveness surface;
- `GET /api/v1/readiness` returns HTTP `503`;
- both payloads set `status="draining"`, `ready=false`, and `draining=true`;
- the `drain` object reports the process-local active-mutation count, timeout, forced flag and
  completion/force reason.

The drain flag is intentionally absent after process restart. A new process starts in
`serving` admission state and must pass #707 reconciliation before `platform-server serve` opens
the normal serving path.

## In-flight disposition policy

| State at drain entry | Shutdown disposition | Restart authority |
| --- | --- | --- |
| mutation admitted but not yet dispatched | may finish only inside the shared deadline; otherwise uncommitted process work disappears and durable state is reconciled | owning canonical subsystem / startup reconciliation |
| dispatched but not acknowledged | do not guess success/failure and do not blindly redispatch during shutdown | distributed/runtime ownership reconciliation; uncertain ownership may block startup |
| running local/reference Run | allow bounded completion; otherwise preserve last durable Run state | `PlatformKernel.recover_all()` |
| running remote Worker execution | do not cancel merely because the Control Plane drains; Worker/job evidence remains authoritative | `DistributedRuntime.reconcile()` before kernel recovery |
| retry/backoff | do not create a shutdown-specific retry loop | owning retry/coordinator runtime after restart |
| waiting dependency | preserve wait/dependency state | `DurablePlanStepCoordinator.reconcile_all()` |
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

The single-node drain projects structured telemetry/timeline evidence:

- `platform.single_node.drain.requested`
- `platform.single_node.drain.entered`
- `platform.single_node.drain.mutable_admission_disabled`
- `platform.single_node.drain.forced`
- `platform.single_node.drain.timeout`
- `platform.single_node.drain.teardown_failed`
- `platform.single_node.drain.completed`
- metric `platform.single_node.drain.in_flight` with a disposition attribute
- `platform.single_node.restart.reconciliation` after the startup reconciliation pass

The restart event records readiness, unresolved Run count, blocked Verification count,
Plan reconciliation count, distributed-job reconciliation count and the resulting disposition.

Observability remains derived state: exporter failure cannot become lifecycle authority.

## Forced-stop recovery

After a forced timeout or hard kill, restart with the **same** `AI_MAP_DATA_DIR`:

```bash
platform-server recover-startup
platform-server serve
```

`serve` runs the same recovery automatically. Use the explicit `recover-startup` command first
when you want to inspect the recovery result without opening the HTTP listener.

If recovery reports an orphaned running Run, follow the existing recovery runbook in
`SINGLE_NODE_RELIABILITY.md`. Do not manually edit SQLite or the startup report.

## What drain does not do

Drain does not:

- create a durable `draining` Task/Run/Step status;
- mark running work failed merely because the process is stopping;
- cancel every remote Worker job;
- resolve Approval or Verification state;
- retry uncertain external side effects during shutdown;
- replace backup/restore recovery;
- provide Control Plane HA or fencing.

Those boundaries are what make forced shutdown safe: durable truth remains in the canonical
owners, and startup reconciliation remains the only ordinary restart authority.
