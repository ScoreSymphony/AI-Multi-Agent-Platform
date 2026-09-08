# Planning graph scale benchmark

This profile extends the issue #440 performance suite after the canonical autonomous-planning work from #439 became available.

It answers a deliberately narrow question: **how does platform-owned planning overhead change as the canonical Plan/Step graph grows?** It does not measure planner quality and it does not treat model inference latency as platform overhead.

## Boundary under test

The benchmark uses the same canonical platform path as ordinary deterministic planning:

```text
Task -> PlanningService.propose()
     -> deterministic Planner proposal
     -> canonical validation
     -> durable planning proposal
     -> PlanningService.activate()
     -> canonical Plan/Step handoff to #384 coordinator
     -> PlanningProposalResourceService.get_resource()
```

The deterministic reference planner performs no model/provider invocation. The report therefore records `provider_invocations: 0`; no synthetic model latency is invented.

The inspection phase uses the canonical planning-proposal Control Plane resource projection rather than reading the JSON planning repository directly. This keeps the measured inspection cost representative of the platform-owned projection that clients consume.

## Default scale sweep

The CLI defaults to the issue #440 planning graph sizes:

```bash
platform-planning-graph-scale \
  --step-counts 10,100,1000 \
  --repetitions 3 \
  --warmup-repetitions 1 \
  --output artifacts/benchmarks/planning-graph-scale.json
```

The default safety ceiling is 2048 Steps per Plan and 20 measured or warmup repetitions. Both are explicit CLI parameters so a deliberate performance investigation can raise or lower the bounds without changing canonical runtime limits.

For a smaller developer check:

```bash
platform-planning-graph-scale \
  --step-counts 10,50 \
  --repetitions 1 \
  --safety-max-steps-per-plan 64 \
  --output /tmp/planning-graph-scale.json
```

## Measurements

Each graph-size point records independent latency distributions for:

- Proposal generation plus canonical validation;
- activation plus durable coordinator handoff;
- canonical planning-proposal inspection/projection.

The report also records process CPU time, traced memory, peak RSS where available, storage growth and open file-descriptor count across the full sweep.

Each measured repetition is invalid unless all of these correctness checks pass:

1. the proposal is `VALIDATED` and contains exactly the requested number of Steps;
2. activation reaches `ACTIVATED` with a canonical Plan ID;
3. the Task points at that activated Plan and the coordinator handoff starts canonical execution;
4. Control Plane inspection returns exactly the generated Step count and the same activation Plan ID.

A faster result that loses Steps, fails activation or fails canonical handoff is therefore reported as failed evidence rather than a successful performance sample.

## Result contract

Machine-readable output validates against:

`docs/schemas/benchmark-planning-graph-scale.v1.schema.json`

The benchmark ID is:

`single-node.planning-graph-scale`

The default environment profile is the single-node reference stack using SQLite kernel/coordinator persistence and JSON planning persistence. Environment metadata remains evidence, not a universal hardware requirement.

## CI and release use

The ordinary test suite runs only tiny graph sizes to validate benchmark correctness and schema stability. The `10/100/1000` sweep is intended for selected integration, manual performance work and release qualification rather than every PR.

The existing `platform-planning-pressure` benchmark remains responsible for concurrent proposal/activation and canonical evidence-to-replanning pressure. This graph-scale profile complements it by isolating Plan size as the independent variable; it does not duplicate the replanning workload.

## Interpreting results

Do not establish universal latency budgets from one machine. Compare runs only when their environment and benchmark configuration are meaningfully comparable, and use repeated measurements before classifying a regression. The operating-envelope tooling under issue #440 remains the owner of cross-run interpretation and evidence-backed budget decisions.
