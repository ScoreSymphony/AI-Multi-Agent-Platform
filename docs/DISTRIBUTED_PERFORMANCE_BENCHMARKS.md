# Distributed Worker / Workspace performance benchmarks

Issue #440 owns benchmark methodology and measured operating-envelope evidence. This profile is
the first scale benchmark built on the completed distributed deployment (#240) and concrete remote
Workspace materialization (#433) contracts.

## What the v1 profile measures

`platform-distributed-scale` composes the shipped distributed runtime instead of a benchmark-only
scheduler or file-copy path:

- one canonical Node and authenticated reporter identity per configured Worker;
- `DeploymentWorkerProtocolService` for authenticated registration and heartbeat;
- `DistributedRuntime` plus `DistributedRegistry` for placement, reservation and reconciliation;
- `MaterializingWorkerDispatcher` on the Control Plane;
- `TransportRemoteWorkspaceMaterializer` and `WorkspaceJobMaterializationResolver` for canonical
  Workspace/Snapshot transfer;
- real Worker-side execution, Workspace and presence endpoints through `DistributedWorkerProcess`;
- `InProcessMessageTransport` as the v1 reference transport so PR evidence isolates distributed
  runtime/materialization behavior from host/network variance.

The measured interval excludes creation of canonical payload fixtures. It includes authenticated
Worker registration, authenticated heartbeat, Workspace-aware dispatch, Worker execution,
terminal reconciliation, result collection and remote cleanup.

The report records:

- Worker registration p50/p95/p99 latency;
- heartbeat p50/p95/p99 latency;
- Workspace-aware dispatch p50/p95/p99 latency;
- terminal reconciliation/result p50/p95/p99 latency;
- Worker Job throughput;
- deterministic placement counts per Worker;
- operation counts per Workspace payload size;
- process CPU, traced memory, peak RSS, descriptor and data-root storage evidence;
- canonical Node, Worker, Worker Job and Run identities for auditability.

## Correctness gates

A report passes only when all configured Workers register and heartbeat successfully, every expected
Worker Job reaches `TERMINAL`, Worker Job and Run identities are unique, every full-width round uses
every eligible Worker exactly once, and all materialized Workspace roots are gone after terminal
result collection/cleanup.

Using one concurrent job per Worker with `concurrency_limit=1` makes each round an explicit
full-width scheduling pressure point. A correctness failure is never converted into a performance
number that can be treated as valid evidence.

## Payload sweeps

Payload sizes are supplied as a strictly increasing comma-separated list. The same canonical
Workspace snapshot is exercised once per round and Worker. The report records the production remote
Workspace transfer chunk size of 64 KiB as fixed v1 metadata; the benchmark does not expose a
benchmark-only chunk override.

Example:

```bash
platform-distributed-scale \
  --worker-count 4 \
  --rounds 3 \
  --payload-sizes-bytes 1024,65536,1048576 \
  --output artifacts/benchmarks/distributed-scale.json
```

Explicit safety limits bound total Worker Job operations and maximum payload size. They prevent an
accidental PR/local invocation from becoming an unbounded load generator.

## Evidence tiers

The tiny PR smoke (`2 Workers x 1 round x 1 KiB`) proves composition, schema and correctness only.
It is not a capacity claim.

Larger retained runs should progressively increase Worker count, rounds and payload sizes on a
recorded reference environment. For true remote/cross-host claims, run a separate profile using the
shipped TCP broker/Worker process deployment and retain host/network metadata. Do not compare the
in-process transport profile to TCP/cross-host results as if they were the same environment.

## Deliberately separate follow-up profiles

This v1 scale block does **not** claim evidence for:

- Worker loss/rejoin or cross-Worker fenced failover under load;
- transient registration/heartbeat/network outages;
- Workspace materialization interruption/failure and recovery under load;
- heterogeneous capability/GPU placement pressure;
- real multi-process or cross-host network throughput limits;
- HA promotion/failover;
- host PSI/zRAM/swap/cgroup pressure;
- release-sized universal operating envelopes.

Those require their own fault or deployment-specific profiles so failure semantics and environment
variance remain visible instead of being mixed into the base scale measurement.
