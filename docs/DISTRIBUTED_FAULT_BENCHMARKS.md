# Distributed Worker / Workspace fault-under-load benchmarks

Issue #440 uses this profile to measure deterministic distributed degradation and recovery semantics on top of the canonical Worker and remote Workspace runtime.

`platform-distributed-faults` is intentionally separate from the baseline `platform-distributed-scale` profile. The scale profile measures steady-state Worker/Workspace overhead; this profile injects explicit faults so degraded results cannot be mistaken for ordinary throughput evidence.

## Production paths exercised

The harness composes the same reference/production-shaped boundaries as the distributed scale profile:

- authenticated `DeploymentWorkerProtocolService` registration and heartbeat;
- `DistributedRegistry` liveness and reservation leases;
- `DistributedRuntime` scheduling, dispatch, reconciliation and terminal result handling;
- real `DistributedWorkerProcess` execution/Workspace endpoints;
- `MaterializingWorkerDispatcher` and `TransportRemoteWorkspaceMaterializer`;
- the reference `InProcessMessageTransport`.

No benchmark-only scheduler, fake Workspace copier or direct mutation of canonical Worker status is used.

## Worker loss and same-ID rejoin

A run first establishes full-width load across all configured Workers. It then stops one actual Worker process while keeping the remaining Workers fresh with authenticated heartbeats. The harness advances the registry's explicit observation time beyond the configured heartbeat timeout and calls canonical reconciliation.

This synthetic observation time avoids wall-clock sleeps while preserving the real liveness rules. The stopped Worker must become `OFFLINE`, while the other Workers remain schedulable.

During the degraded interval the harness submits exactly one concurrent job per remaining Worker. No degraded job may be placed on the lost Worker.

The Worker is then restarted as a new `DistributedWorkerProcess` using the same canonical Node/Worker registration. Re-registration and heartbeat must restore that same Worker ID rather than creating replacement canonical identity. A subsequent full-width round must use the rejoined Worker again.

This profile proves **loss/rejoin and degraded scheduling**. It does not claim cross-Worker fenced redispatch of already-running work; that is a distinct failover scenario.

## Workspace materialization failure and recovery

After rejoin, the harness makes the reference MessageTransport unavailable through its documented deterministic outage hook immediately before a Workspace-aware dispatch.

The expected boundary behavior is:

- the dispatch surfaces `ContractError(ErrorCode.UNAVAILABLE, retryable=True)`;
- the canonical distributed dispatch record becomes `LOST` because the external dispatch outcome failed after ownership was persisted;
- Worker execution is never reached, so the failed record has no execution handle;
- no duplicate Worker Job or Run identity is created.

The transport is then restored. The harness advances explicit observation time beyond the reservation TTL, refreshes Worker heartbeats, and reconciles so the stranded reservation no longer consumes capacity. A new canonical recovery job must dispatch successfully, reach terminal state, return a result and leave no remote Workspace materialization behind.

The recovery uses a new Worker Job/Run identity. It does not silently replay the failed dispatch under the same ownership record.

## Metrics and correctness gates

The report records:

- pre-fault dispatch p50/p95/p99;
- Worker-loss reconciliation latency;
- degraded dispatch p50/p95/p99;
- same-ID Worker re-registration latency;
- post-rejoin dispatch p50/p95/p99;
- Workspace failure-detection latency;
- Workspace recovery dispatch latency;
- placement counts, successful-job throughput and process resource evidence;
- canonical Worker, Worker Job and Run references.

A report passes only if all identity, liveness, degraded-placement, canonical-error, recovery, cleanup and uniqueness invariants pass. Expected fault injection is recorded as correctness evidence rather than counted as a successful job.

Example:

```bash
platform-distributed-faults \
  --worker-count 3 \
  --pre-fault-rounds 2 \
  --degraded-rounds 2 \
  --post-rejoin-rounds 2 \
  --payload-bytes 65536 \
  --output artifacts/benchmarks/distributed-faults.json
```

Explicit operation and payload bounds prevent accidental unbounded local/CI load.

## Evidence scope

The PR smoke uses a tiny three-Worker, one-round, 1 KiB fixture. It proves composition, fault semantics, schema and correctness only. It is not a release-sized capacity claim.

This reference profile also does not measure TCP/cross-host network limits, heterogeneous GPU/capability placement, HA promotion or host-level pressure. Those remain separate #440 evidence blocks so environment variance and failure semantics stay visible.
