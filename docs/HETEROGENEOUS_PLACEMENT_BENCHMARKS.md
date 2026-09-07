# Heterogeneous placement performance benchmarks

Issue #440 owns platform performance evidence. This profile adds the capability/resource placement block that remains deliberately separate from the existing distributed Worker/Workspace scale benchmark.

## Scope

`platform-heterogeneous-placement` exercises the canonical `DeterministicScheduler`, `DistributedRegistry`, `NodeRecord`, `WorkerRecord` and `JobRequirements` contracts. It does not introduce a benchmark-only placement algorithm.

The reference topology contains three intentionally different Node roles:

- a CPU-only Node;
- an accelerator/model-capable Node with generic GPU/VRAM metadata;
- a browser/network-capable Node.

The Workers intentionally overlap in executor, capability, runtime and benchmark-model support. This prevents a unique executor name from predetermining placement and makes the canonical resource, label and network constraints materially responsible for rejecting decoy candidates.

The topology is synthetic and provider-neutral. Vendor names, VPS SKUs and personal hostnames are not part of canonical identity.

## Workload profiles

Each iteration runs four placement profiles:

1. `cpu-only` requires general CPU execution, the `cpu-primary` label, Python runtime and `gpu=forbidden`. The accelerator Worker deliberately supports the same logical execution surface and must be rejected by the CPU-only GPU constraint.
2. `gpu-inference` requires model execution, model capability, a generic accelerator, at least 4 GiB VRAM and the benchmark model/runtime. CPU and browser Workers deliberately support the same logical model surface but must be rejected because they have no accelerator.
3. `browser-network` requires browser execution/capability, network availability, the browser label and browser runtime. The CPU Node carries the browser label but has no network; the accelerator Node has network but lacks the browser label. Only the browser Node satisfies both hard constraints.
4. `unschedulable-vram` requires 64 GiB VRAM although the reference accelerator exposes only 24 GiB; it must be rejected without leaving a reservation behind.

Every successful sample releases its canonical reservation before the next sample. This keeps the profile focused on placement latency rather than intentional concurrency saturation, which belongs to other #440 stress/scale profiles.

## Constraint evidence and correctness

The benchmark records the canonical `SchedulingDecision` returned by the timed `schedule()` call. It therefore captures candidate rejection evidence without running a second scheduler pass inside the timed interval. Only the intentionally unschedulable profile performs a post-failure `evaluate()` call because `NoEligibleWorkerError` does not carry the decision object.

For every iteration the harness requires these decoy signals:

- `cpu-only:gpu:gpu_required`;
- `cpu-only:browser:label_mismatch`;
- `gpu-inference:cpu:gpu_required`;
- `gpu-inference:browser:gpu_required`;
- `browser-network:cpu:network_unavailable`;
- `browser-network:gpu:label_mismatch`;
- `unschedulable-vram:gpu:vram_insufficient`.

A missing expected rejection increments `constraint_evidence_failures` and fails correctness. This distinguishes “the expected Worker happened to win” from evidence that the intended heterogeneous constraint actually eliminated the decoy candidate.

The report records:

- overall placement p50/p95/p99 latency;
- p50/p95/p99 latency for every workload profile;
- placement counts by Worker role;
- canonical scheduler rejection-code counts across rejected candidates;
- profile/role/constraint evidence counts;
- process CPU, traced memory, peak RSS, descriptor and benchmark-root storage evidence;
- canonical Node and Worker IDs used by the run.

A report passes only when every schedulable profile lands on its expected heterogeneous role, every intentionally impossible profile is rejected, every expected constraint signal is observed, the configured operation count is completed, and no scheduler reservation remains active after the run.

## Running locally

```bash
platform-heterogeneous-placement \
  --iterations-per-profile 250 \
  --output artifacts/benchmarks/heterogeneous-placement.json
```

The default is intentionally bounded. `--safety-max-operations` prevents accidental unbounded sweeps.

The machine-readable output conforms to `docs/schemas/benchmark-heterogeneous-placement.v1.schema.json`.

## Interpretation

This benchmark measures platform scheduler overhead and placement correctness for heterogeneous capability/resource metadata. It is not a raw GPU/model throughput benchmark and it does not claim universal hardware capacity.

Absolute latency values are comparable only when the recorded environment is sufficiently comparable. Relative regressions should be evaluated using the general #440 performance-regression policy rather than treating tiny scheduler timing differences as release blockers.

## Relationship to distributed scale

`platform-distributed-scale` continues to own authenticated Worker registration/heartbeat, Workspace-aware dispatch, remote Workspace materialization and result/cleanup scale evidence. This profile supplies the separate heterogeneous capability/GPU placement pressure block without mixing scheduler-only latency with network or Workspace-transfer variance.

Worker loss/rejoin under load, Workspace materialization failure/recovery, and true cross-host transport throughput remain separate #440 work items.
