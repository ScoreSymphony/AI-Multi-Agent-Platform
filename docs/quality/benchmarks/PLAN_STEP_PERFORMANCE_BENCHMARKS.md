# Durable Plan/Step performance benchmarks

This document extends issue #440 after the durable coordinator from #384 became available. The profile measures the canonical `PlatformKernel` + `DurablePlanStepCoordinator` path with the reference SQLite kernel and coordinator stores. It does not benchmark repository methods in isolation and it does not include model-provider or planner-service latency in the measured coordination window.

## Deterministic graph profiles

`platform-plan-step` provides three bounded graph shapes:

- `linear`: `size` is the number of Steps in one dependency chain;
- `fan-out`: one root activates `size` independent leaves;
- `fan-in`: one root activates `size` leaves and a final barrier depends on every leaf.

Planning still goes through the canonical `Orchestrator` contract and `PlatformKernel.plan_task()`, which allocates the canonical Plan and Step IDs. The benchmark fixture only supplies deterministic proposal content. The measured window begins after canonical planning so planning/provider time is not misreported as coordinator overhead.

Each Step attempt is created and started through the real kernel APIs used by `DurablePlanStepCoordinator`. Deterministic success is persisted with `PlatformKernel.record_run_outcome()`, then consumed with `DurablePlanStepCoordinator.observe_run()`. The benchmark records those two latency distributions separately.

## Metrics

The v1 report records:

- Plan registration/initial activation p50/p95/p99;
- canonical run-outcome persistence p50/p95/p99;
- coordinator observation/progression p50/p95/p99;
- completed Steps per second for the measured coordination window;
- peak simultaneously running Step width;
- process CPU, traced memory, optional peak RSS, open descriptor count and SQLite storage growth;
- canonical Task, Plan, Step and Run identifiers for bounded evidence inspection.

The environment fingerprint is descriptive evidence, not a universal capacity requirement.

## Correctness requirements

A point passes only when all of the following hold:

- every expected Step reaches `succeeded`;
- exactly one canonical `run.created` event exists per Step for these no-retry fixtures;
- every Step has one unique canonical Run ID;
- every dependency completes before its dependent Step;
- the owning canonical Task reaches `succeeded`;
- the measured active width equals the deterministic graph expectation (`1` for linear, `size` for fan-out/fan-in);
- no benchmark operation reports an error or stalls with unfinished work and no active Run.

A faster result that violates any of these invariants is failed evidence.

## Usage

```bash
platform-plan-step \
  --scenario linear \
  --size 100 \
  --timeout-seconds 30 \
  --safety-max-size 1000 \
  --output artifacts/benchmarks/plan-step-linear-100.json

platform-plan-step \
  --scenario fan-out \
  --size 64 \
  --timeout-seconds 30 \
  --safety-max-size 256 \
  --output artifacts/benchmarks/plan-step-fan-out-64.json

platform-plan-step \
  --scenario fan-in \
  --size 64 \
  --timeout-seconds 30 \
  --safety-max-size 256 \
  --output artifacts/benchmarks/plan-step-fan-in-64.json
```

`--safety-max-size` is an explicit host-protection bound. Passing a large point is evidence for that environment only, not a universal platform scale claim.

The machine-readable schema is `docs/schemas/benchmark-plan-step.v1.schema.json`.

## Scope boundary

This first #384-backed block intentionally covers graph activation/progression only. Retry-heavy coordination, many simultaneously waiting Steps, concurrent multi-Plan coordination, coordinator claim contention and restart/reconciliation with a large active workflow set remain progressive #440 profiles. They should extend this evidence model instead of being folded into the initial graph-shape benchmark and obscuring attribution.
