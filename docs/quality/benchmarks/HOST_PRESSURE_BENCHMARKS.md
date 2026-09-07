# Host-pressure observer benchmarks

Issue #440 owns performance, load, stress and operating-envelope evidence. Issue #500 now supplies the stable portable pressure/admission contract and Linux collector that this profile observes.

`platform-host-pressure-observe` is the first **real-host** host-pressure benchmark surface. It is intentionally an observer rather than a load generator: it samples the existing read-only `LinuxHostPressureProvider`, evaluates the current portable admission policy against each sampled snapshot and writes bounded machine-readable evidence. It never allocates memory to create pressure, changes swap/zRAM/cgroup configuration, mutates kernel settings or attempts an OOM.

## What it records

Each bounded observation window records:

- normalized `healthy` / `elevated` / `critical` / `unknown` pressure state;
- portable pressure signals and values/units;
- host-specific `linux.host_pressure` evidence when provider metadata is enabled, including available PSI, swap/paging, zRAM, filesystem/inode, descriptor and cgroup-v2 counters already exposed by the #500 collector;
- pressure-admission action and structured reason codes for a configured workload class;
- the corresponding canonical scheduler accept/reject result without creating a reservation;
- state transitions;
- first observed elevated/critical pressure to subsequent healthy recovery latency when both occur in one observation window;
- safe environment metadata and correctness/safety evidence.

The observer does not claim Control Plane API latency. Existing API-pressure profiles remain the owner of HTTP/API responsiveness measurements; dedicated-host runs may correlate their reports by timestamp.

## Run on a dedicated Linux host

A simple observation window:

```bash
platform-host-pressure-observe \
  --duration-seconds 300 \
  --sample-interval-seconds 1 \
  --output artifacts/benchmarks/host-pressure-observer.json
```

The default safety ceiling is one hour and the default sample cap is 3601 samples. Requests whose duration exceeds `--safety-max-duration-seconds`, or whose duration/interval combination can exceed `--max-samples`, are rejected before collection begins.

Provider-specific evidence can be omitted when only the portable contract is needed:

```bash
platform-host-pressure-observe \
  --duration-seconds 300 \
  --sample-interval-seconds 1 \
  --omit-provider-metadata \
  --output artifacts/benchmarks/host-pressure-portable.json
```

Custom mounted proc/sys/cgroup views are supported for containerized or constrained deployments:

```bash
platform-host-pressure-observe \
  --proc-root /host/proc \
  --sys-root /host/sys \
  --cgroup-root /host/sys/fs/cgroup \
  --storage-path /host \
  --duration-seconds 300 \
  --sample-interval-seconds 1 \
  --output artifacts/benchmarks/host-pressure-mounted.json
```

## Pressure and recovery experiments

For a dedicated-host experiment, start the observer before the independently controlled workload, let the external workload produce the intended pressure window, remove that workload while observation continues, and keep the observer running through recovery.

The platform observer itself remains read-only. This separation is deliberate: a memory allocator, cgroup stress fixture or other destructive load generator must have its own explicit safety boundary and must not be smuggled into ordinary PR CI.

One observation window can therefore capture transitions such as:

```text
healthy -> elevated -> critical -> healthy
```

When elevated or critical pressure is observed and a later healthy sample appears, the report emits `recovery_latency_seconds`. A run that never experiences pressure is still valid baseline evidence; it reports `observed_pressure=false` rather than fabricating a recovery measurement.

## Comparing host configurations

The observer is suitable for repeatable manual/release comparisons such as:

- no zRAM vs zRAM;
- zRAM-only vs zRAM plus disk-backed swap;
- cgroup-unconstrained vs an operator-defined `memory.high` profile;
- light vs heavy external workload;
- pre-pressure, sustained-pressure and post-pressure recovery behavior.

Configuration labels and hardware/OS details belong in the retained benchmark artifact/run metadata. Do not turn one measured machine profile into a universal platform requirement.

## What remains separate

This slice does **not** itself generate or certify:

- bounded `memory.max` / OOM-containment stress;
- a paging/thrashing load generator;
- zRAM or swap tuning;
- cgroup mutation;
- release-sized long soak evidence;
- universal performance budgets.

Those remain progressive #440 dedicated-host/release work. The observer provides the versioned evidence surface needed to measure them without coupling benchmark methodology to one VPS, kernel configuration or host-tuning mechanism.

The report schema is `docs/schemas/benchmark-host-pressure-observer.v1.schema.json`.
