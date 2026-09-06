# Authenticated Control Plane API pressure benchmarks

Issue #440 requires client-facing API evidence in addition to lifecycle, persistence, provider-fault, and durable-coordination benchmarks. This profile measures the stable single-node Control Plane request path without replacing it with repository or authorization-engine microbenchmarks.

## What this profile measures

`platform-api-pressure` builds the production-shaped single-node deployment, bootstraps one local administrator, creates a canonical Project, and seeds canonical Tasks through the authenticated `/api/v1/tasks` endpoint. Seeding and browser login happen before the measured interval.

The measured workload rotates through four operation classes:

1. **Bearer-authenticated Task list** — `GET /api/v1/tasks` with the normal personal access token boundary.
2. **Browser-session Task list** — the same list request through the server-issued session cookie. This isolates the ordinary per-request session-authentication path from password hashing; login itself is not part of the measured request latency.
3. **Authorization-protected Task detail** — `GET /api/v1/tasks/{task_id}` through bearer authentication. This is a production resource-access measurement, not a standalone policy-engine microbenchmark.
4. **Full cursor pagination scan** — repeatedly follows the Control Plane `next_cursor` until every seeded Task has been observed exactly once.

The v1 profile uses the canonical `PageQuery` limit range and therefore accepts page sizes from 1 through 200.

## Correctness gates

A report passes only when:

- every requested composite operation completes;
- the final canonical Task total equals the seeded Task count;
- all four operation classes were actually measured;
- every full pagination scan observes every seeded Task exactly once;
- pagination returns no duplicate canonical Task IDs;
- every measured HTTP response exposes an `x-request-id`;
- measured request IDs are globally unique within the run.

Authentication failures, authorization failures, cursor loops, missing response identities, incomplete scans, duplicate IDs, or unexpected Task counts are retained as benchmark errors rather than discarded as timing noise.

## Metrics

The report separates:

- total composite-operation p50/p95/p99 latency;
- bearer-list p50/p95/p99 latency;
- browser-session-list p50/p95/p99 latency;
- authorization-protected detail p50/p95/p99 latency;
- full pagination-scan p50/p95/p99 latency;
- individual pagination-page p50/p95/p99 latency;
- completed composite operations per second;
- process CPU time;
- traced current/peak memory;
- peak RSS where available;
- SQLite data-root size before/after the measured interval;
- open file descriptors where available;
- request/category/cursor correctness counters.

Provider or model latency is not present in this profile. It measures the platform-owned Control Plane/authentication/authorization/pagination path.

## Running locally

A bounded example:

```bash
platform-api-pressure \
  --seed-tasks 100 \
  --operations 40 \
  --concurrency 10 \
  --page-size 25 \
  --warmup-operations 4 \
  --timeout-seconds 30 \
  --output artifacts/benchmarks/api-pressure.json
```

`--safety-max-seed-tasks` defaults to 10,000 and prevents an accidental oversized local run. Increase it only deliberately for a documented benchmark environment.

## CI versus operating-envelope evidence

The dedicated PR smoke uses only a tiny seeded population and small concurrency. Its purpose is to prove benchmark semantics, authentication modes, cursor traversal, schema stability, and correctness gates.

It is **not** evidence for a universal platform capacity limit. Release or operating-envelope work should retain repeated larger runs on documented reference hardware, for example increasing seeded Task populations and concurrency while keeping the exact benchmark version, page size, environment metadata, and correctness gates visible. Performance budgets should be derived from those retained measurements rather than invented in PR CI.

## Scope boundaries

This profile does not:

- benchmark password hashing as request throughput;
- bypass the authenticated Control Plane to query SQLite directly;
- claim that Task-detail latency is pure authorization-engine cost;
- introduce a second pagination implementation;
- benchmark distributed Worker transport or remote Workspace materialization;
- claim physical-host or deployment-independent capacity.

Distributed Worker/Workspace scale belongs to the supported #240/#433 deployment path once its current hardening state is accepted. Host-pressure and HA profiles remain separate optional #440 families.
