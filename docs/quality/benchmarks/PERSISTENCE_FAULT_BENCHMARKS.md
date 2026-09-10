# Transient persistence-fault benchmarks

Issue #440 owns performance, load, stress and scalability evidence. This profile adds deterministic
durable-state fault/recovery evidence on top of the platform-owned `EventRepository` boundary and the
real `SqliteKernelRepository` reference implementation.

## What the v1 profile measures

`platform-persistence-fault` creates canonical Tasks and moves them to `READY` through the normal
`PlatformKernel` lifecycle. A benchmark-only decorator wraps the `EventRepository` boundary and
injects one retryable `TRANSIENT_FAILURE` for selected idempotent commands.

Three deterministic modes are available:

- `before-commit`: the injected failure occurs before the delegate repository is called. Retrying the
  identical command must create exactly one event and one durable command record.
- `after-commit`: the SQLite transaction commits successfully and the benchmark then simulates a
  lost/failed response. Retrying the identical command must discover the durable idempotency record
  and return the existing canonical state without a second event.
- `mixed`: selected commands alternate between the two fault boundaries.

The benchmark driver retries only the exact same logical command, with the same idempotency key and
canonical Task ID. This is evidence for the existing idempotency/recovery contract; it does **not**
add a hidden automatic retry policy to `PlatformKernel`.

## Correctness gate

Performance numbers are valid only when all configured Tasks recover and a fresh
`SqliteKernelRepository` reopened over the same database proves:

- every expected Task stream exists;
- every Task is `READY`;
- each history contains exactly one `task.created` and one `task.ready`;
- the create command is durable under the canonical `task:create` scope;
- the ready command is durable under the canonical Task-ID scope;
- every planned fault was injected exactly once;
- every observed retryable persistence failure recovered within the configured attempt bound;
- no retry was exhausted and no unexpected error occurred.

The post-run verification deliberately uses the public repository/kernel contracts. The benchmark
does not query or mutate private SQLite tables.

## Metrics

The report records:

- logical create/ready operation latency p50/p95/p99;
- individual persistence-attempt latency p50/p95/p99;
- fault-to-successful-recovery latency p50/p95/p99;
- recovered logical-operation throughput;
- planned/injected before-commit and after-commit fault counts;
- repository/delegate commit-call counts;
- retryable-failure, recovered-operation and exhausted-retry counts;
- process CPU, traced memory, peak RSS, open descriptors and data-root storage evidence;
- post-reopen canonical integrity results.

## Example

```bash
platform-persistence-fault \
  --task-count 50 \
  --concurrency 8 \
  --failure-mode mixed \
  --failure-every 3 \
  --max-attempts 3 \
  --output artifacts/benchmarks/persistence-fault.json
```

The ordinary PR smoke is intentionally tiny and deterministic. Larger retained runs may increase
Task count and concurrency within the explicit safety ceilings.

## Scope boundary

This profile targets deterministic transient failure/recovery at the canonical persistence
interface. It is **not** evidence for operating-system-level SQLite lock saturation, filesystem
failure, disk-full behavior, real storage latency spikes, or a stronger production database backend.

Real competing SQLite writers are covered separately by `platform-persistence-contention`, which
uses independent canonical kernel/repository instances sharing the same WAL database and reports
writer latency, throughput, surfaced busy/locked errors, conflicts and post-reopen integrity. Keeping
that profile separate prevents a fault decorator from being misrepresented as real lock-pressure
evidence.

Filesystem failures, disk-full behavior and externally induced storage-latency spikes remain outside
these deterministic reference-backend profiles and require controlled host-level evidence.

The benchmark remains provider-neutral at the platform boundary, requires no paid service and does
not make SQLite canonical. SQLite is used only as the shipped durable reference implementation for
this v1 evidence profile.

The report schema is
`docs/schemas/benchmark-persistence-fault.v1.schema.json`.
