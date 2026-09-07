# Control Plane HA failover performance benchmarks

Issue #440 owns performance and scalability evidence. This optional profile measures the completed
#89 active/passive Control Plane failover contract without turning the reference coordination
fixture into a production HA claim.

## Scope

`platform-ha-failover` uses the canonical #89 `ControlPlaneFailoverService`,
`InMemoryCoordinationProvider`, `PlatformKernel`, SQLite kernel repository and
`DistributedRuntimeFailoverReconciler`. Each repetition:

1. starts one active and one standby Control Plane service;
2. prepares a configurable set of canonical Tasks and Runs through the normal kernel lifecycle and
   seeds one canonical Worker reservation per Task;
3. advances the deterministic coordination clock past the active lease and reservation TTL;
4. promotes the standby and measures promotion plus distributed reconciliation latency;
5. proves the promotion reconciler expires every seeded stale reservation;
6. proves the old leader is rejected by the fencing contract;
7. reconstructs the kernel over the same durable state and replays the identical create/ready/start
   idempotency keys while measuring per-Task recovery replay latency;
8. verifies no duplicate Task, Run, lifecycle dispatch or canonical create/run event appears;
9. removes coordination availability and measures fail-closed authority rejection;
10. restores coordination, advances beyond the previous lease and measures promotion of a new
    instance under a strictly newer fencing epoch.

This deliberately exercises canonical lifecycle, scheduler reservation and HA contracts instead of
benchmarking private SQLite tables or a benchmark-only leader implementation.

## Metrics

The report records:

- promotion plus distributed reconciliation latency p50/p95/p99;
- reconciled stale Worker reservation count;
- stale-leader fencing rejection latency p50/p95/p99;
- per-Task idempotent recovery replay latency p50/p95/p99;
- coordination-outage authority rejection latency p50/p95/p99;
- post-outage recovery promotion latency p50/p95/p99;
- recovered Task throughput;
- process CPU, traced-memory, peak-RSS, descriptor and data-root storage evidence;
- initial, promoted and recovery fencing epochs for every repetition.

Correctness is a hard gate. Performance numbers are invalid when identity, history, reconciliation,
fencing, fail-closed coordination or idempotent dispatch invariants fail.

## Example

```bash
platform-ha-failover \
  --active-task-count 50 \
  --repetitions 5 \
  --safety-max-tasks 250 \
  --output artifacts/benchmarks/ha-failover.json
```

`--lease-ttl-seconds` controls logical lease duration. The benchmark does not wait for that duration
in real time; it advances the deterministic #89 coordination clock. `--timeout-seconds` is a real
wall-clock safety bound for the entire benchmark run. A timeout is retained as a structured failed
report, including partial completed-repetition evidence when available.

## Evidence tiers

The PR smoke uses a tiny Task set and proves composition, schema and correctness only. It is not a
capacity claim.

Larger retained runs may increase Task count and repetitions on a recorded comparable environment.
Absolute promotion/replay numbers from different hardware must not be treated as directly comparable
without environment checks.

## Important limitation: this is not real multi-host HA

The shipped #89 `InMemoryCoordinationProvider` is intentionally process-local. Therefore this
profile proves and measures deterministic HA **semantics**—fencing, promotion, reconciliation,
durable command replay and fail-closed coordination—but it does not prove independent-process or
cross-host durability.

Production-shaped multi-instance coordination and shared durable state remain owned by #566. Any
future real multi-process/multi-host #440 profile must use that production-shaped seam and record its
deployment/network/storage environment separately. The process-local reference report must never be
presented as two-host HA capacity evidence.

## Relationship to other #440 profiles

- `platform-distributed-scale` measures steady-state Worker/Workspace scale.
- `platform-distributed-faults` measures Worker loss/rejoin and Workspace transport
  failure/recovery.
- `platform-heterogeneous-placement` measures capability/resource placement pressure.
- `platform-ha-failover` measures the optional #89 Control Plane failover contract.
- #500 now provides the portable host-pressure/admission seam and bounded deterministic pressure
  evidence; host-specific PSI/zRAM/swap/cgroup operating-envelope runs remain a separate manual or
  release qualification profile.
- real cross-host distributed operating-envelope evidence remains deployment-specific and is
  complemented by #562.