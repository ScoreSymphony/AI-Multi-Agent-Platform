# Single-node reliability and startup recovery

Issue #707 hardens the ordinary #39 single-node / single-Control-Plane deployment without making high availability a baseline dependency.

This document describes the first reliability slice: **ordinary startup reconciliation after an abnormal process exit**. Disaster-restore recovery remains owned by #40 and uses its separate restore marker/report lifecycle.

## Startup invariant

Before `platform-server serve` opens the normal HTTP serving path, the deployment reconciles the durable runtime through the existing subsystem owners and then scans every canonical Task stream:

```text
persisted single-node data root
        |
        +-- distributed_runtime.reconcile()   [when enabled]
        |      - expire stale heartbeats
        |      - reconcile Worker jobs
        |      - expire stale reservations
        |
        +-- coordination.reconcile_all()
        |      - reconcile active Plan/Step Runs
        |      - resume due retries/deadline waits
        |      - advance safe canonical work
        |
        `-- kernel.recover_all()
               |
               +-- queued Run --------------------> remains queued
               +-- starting Run + backend found --> reconcile snapshot
               +-- starting Run + backend absent -> canonical redispatch
               +-- running Run + backend found ---> reconcile snapshot
               +-- running Run + backend absent --> recovery_required + BLOCK
               +-- terminal Run ------------------> unchanged
        |
        v
recovery/startup-report.json
        |
        +-- ready_for_service=true  -> serving may start
        `-- ready_for_service=false -> serving is refused
```

The ordering is deliberate: distributed Worker liveness/reservation truth is refreshed first; the platform-owned Plan/Step coordinator then reconciles its durable coordination state; the final kernel-wide pass covers Tasks and Runs that are not part of an active Plan as well.

The startup gate does not invent a terminal outcome for a Run whose canonical state says `running` but whose execution backend cannot be found. That case is `orphaned_reconciliation_required` and must be resolved explicitly.

## Commands

Run the ordinary startup reconciliation without opening the HTTP server:

```bash
platform-server recover-startup
```

A blocked result exits non-zero and records the exact unresolved Run IDs in:

```text
<data-dir>/recovery/startup-report.json
```

The report also records how many active Plans and distributed Worker jobs were inspected by their existing reconciliation owners.

After investigating an orphaned Run, an operator may terminalize only the exact Task/Run pair named by the current blocked report:

```bash
platform-server resolve-startup-run \
  --task-id task_... \
  --run-id run_... \
  --resolution failed \
  --reason "original execution process disappeared"
```

`cancelled` is also accepted when cancellation is the correct canonical outcome.

The resolution uses `PlatformKernel.record_run_outcome()` with a deterministic idempotency key. It never edits the event store directly. After the transition, the complete ordinary startup reconciliation pass is rerun automatically. Serving remains blocked if any unresolved Run remains.

## Recovery outcomes

This slice deliberately distinguishes the outcomes already supported by the canonical runtime owners:

| Runtime evidence | Recovery action | Startup effect |
| --- | --- | --- |
| Run is queued | keep canonical queued state | non-blocking |
| Run is starting and backend state is absent | redispatch through the canonical lifecycle boundary | non-blocking if recovery succeeds |
| Run is starting/running and backend state exists | reconcile canonical state from backend snapshot | non-blocking if recovery succeeds |
| Run is running and backend state is absent | mark `recovery_required` | **blocking** |
| Run is terminal | preserve terminal state | non-blocking |
| active Plan/Step state exists | reconcile through `DurablePlanStepCoordinator` | non-blocking if reconciliation succeeds |
| distributed Worker state exists | refresh liveness/jobs/reservations through `DistributedRuntime` | non-blocking if reconciliation succeeds |

A reconciliation exception itself is fail-closed: `platform-server serve` does not open the HTTP serving path when a required recovery owner cannot complete its pass.

Broader #707 work will extend this policy to additional session/materialization transient state, persistence failures and uncertain external side effects. Those must remain explicit instead of being collapsed into a generic `retry everything` rule.

## Idempotency and fail-closed behavior

`reconcile_single_node_startup()` is safe to invoke repeatedly. Repeated reconciliation delegates authoritative mutations to the existing kernel, coordinator and distributed-runtime idempotency/reconciliation semantics and rewrites only the deployment-local diagnostic report.

The report is written atomically using a temporary file plus replacement. An unreadable or incompatible report is never accepted as authorization for manual Run resolution.

`resolve-startup-run` requires all of the following before it can mutate canonical state:

1. a current startup recovery report exists;
2. `ready_for_service` is false;
3. the exact Run ID appears in `unresolved_run_ids`;
4. the exact Task/Run entry has disposition `orphaned_reconciliation_required`;
5. the requested terminal transition goes through the canonical kernel API.

This prevents the recovery command from becoming a general-purpose way to terminalize arbitrary live Runs.

## Relationship to restore recovery

Normal startup recovery and disaster-restore recovery are separate gates:

```text
platform-server serve
        |
        +-- #40 post-restore gate, when a restore marker/report requires it
        |
        `-- #707 ordinary startup gate, on every normal serve
```

A restored deployment must clear the #40 integrity/readiness gate before ordinary startup resolution can be used. This avoids using the narrower #707 path to bypass restore integrity validation.

## Relationship to HA

No leader election, standby Control Plane, fencing epoch or shared HA persistence is introduced here. #566 remains optional and owns real multi-instance Control Plane HA.

The reliability principle is intentionally stronger than "HA will recover it later": a single Control Plane should first be able to crash, restart and return to a deterministic canonical state on its own.

## Remaining #707 scope

This startup slice does **not** close #707. Remaining reliability work includes, among other items:

- graceful drain/shutdown hardening;
- stale non-Worker session/materialization cleanup;
- explicit uncertain-side-effect recovery states;
- persistence/filesystem fault injection and recovery;
- provider/dependency failure isolation and bounded retries;
- health/readiness integration while reconciliation is in progress;
- operator diagnostics beyond the startup report;
- repeated hard-kill/restart endurance testing;
- reusable failure-injection fixtures and #46 reliability evidence.
