# Reference Host Benchmark Campaigns

Issue #440 already provides the canonical single-node sweep, endurance and operating-envelope
building blocks. `platform-reference-host-campaign` composes those existing harnesses into one
repeatable **host-local evidence campaign**. It does not introduce a second load generator,
remote orchestration layer or VPS/provider-specific benchmark contract.

## Purpose

A reference-host campaign answers a narrower question than a universal capacity benchmark:

> What operating envelope did this exact platform commit demonstrate on this documented host,
> using the fixed campaign profile and the canonical single-node benchmark path?

The result is retained evidence, not a claim that every host has the same capacity. Absolute
throughput and latency remain host-specific. Cross-host comparison is performed only through the
existing operating-envelope catalog, which preserves each host separately.

## Fixed profiles

### `release`

The release profile is intentionally fixed to the release-sized values already documented for
#440:

- concurrency levels: `1,10,50,100`;
- operations per concurrency level: `500`;
- sweep repetitions: `5`;
- sweep warmup operations: `20`;
- per-operation timeout: `60s`;
- soak duration: `3600s`;
- soak sample interval: `10s`;
- soak maximum operations: `20000`;
- soak concurrency: `10`;
- soak seed tasks: `100`;
- soak warmup operations: `20`;
- soak read/write weights: `4/1`.

The release profile requires an explicit `--work-dir`. This makes the operator choose the storage
path actually being measured instead of silently benchmarking an arbitrary temporary filesystem.
The directory must be fresh/empty and must be disjoint from the evidence output directory.
Explicit work data is intentionally left in place after the command exits so a failed or unusual
run can be inspected; start the next campaign with a new empty work directory.

The campaign also binds the resulting environment fingerprint to the **storage target containing
that work directory**. On Linux, the retained metadata contains the filesystem type, total
filesystem capacity and a SHA-256 fingerprint derived from the selected mount's privacy-sensitive
identity inputs, including its mount source, root, option sets and `major:minor` device identity.
Those raw identity inputs, including the device identity, mount source/root and work-directory
path, are not persisted. On platforms without Linux mount metadata, a privacy-safe filesystem-stat
fallback is fingerprinted instead. This prevents campaigns performed against materially different
storage mounts from being treated as the same reference environment merely because CPU, RAM, OS
and Python match.

Example:

```bash
platform-reference-host-campaign \
  --profile release \
  --host-label reference-linux-a \
  --platform-commit "$(git rev-parse HEAD)" \
  --work-dir /path/on/measured-storage/ai-map-benchmark-work \
  --output-dir artifacts/benchmarks/reference-linux-a
```

The exact tested commit is mandatory. Do not substitute `unknown`, and do not reuse results from a
different commit as if they belonged to the current release candidate.

### `smoke`

The smoke profile is deliberately tiny. It exists only to exercise the campaign contract in CI
and local development. It does **not** establish a real-host capacity or release operating-envelope
claim and must not be duplicated across CI runners and presented as cross-host evidence.

## Evidence layout

A successful campaign writes:

```text
<output-dir>/
  campaign.json
  operating-envelope.json
  soak.json
  sweep/
    summary.json
    c-<concurrency>-r-<repetition>.json
```

`campaign.json` records:

- the exact campaign/profile version and tested platform commit;
- a human-readable host label;
- the complete fixed campaign configuration plus its canonical SHA-256 digest;
- the environment metadata and environment fingerprint from the derived operating envelope,
  including the privacy-safe `storage_target` identity of the measured work-directory filesystem;
- SHA-256 digests for the sweep summary, soak report and operating-envelope report;
- `claim_semantics = "single-host-tested-evidence-only"`;
- `budget_status = "not-established"`.

The same storage-aware environment and fingerprint are written into `operating-envelope.json`.
Later same-host reproducibility and regression analysis therefore rejects a storage-target change
through the existing environment-fingerprint comparability contract; no separate ad-hoc storage
comparison path is required.

The host label is descriptive evidence metadata, not a machine identity. Use a stable label that
does not contain credentials, private addresses or other values that should not appear in retained
or public benchmark artifacts.

A partial or failed campaign may leave partial files behind. Those files are diagnostic evidence,
not a successful campaign. The runner refuses to reuse a non-empty output directory so a retry
cannot silently mix artifacts from different executions.

## Cross-host workflow

Run the same `release` profile for the same exact platform commit independently on each intended
reference host. Do not edit the generated environment metadata or fingerprints to make hosts look
comparable. When repeating campaigns on one host, use fresh work directories on the same intended
storage target if the runs are meant to form one reproducibility series.

After at least two independent host campaigns exist, build the cross-host catalog from their
operating envelopes:

```bash
platform-operating-envelope-catalog \
  --host reference-linux-a=artifacts/benchmarks/reference-linux-a/operating-envelope.json \
  --host reference-linux-b=artifacts/benchmarks/reference-linux-b/operating-envelope.json \
  --output artifacts/benchmarks/reference-host-catalog.json
```

The campaign configuration digest identifies the complete host-local workload profile. The
cross-host catalog still enforces its own operating-envelope comparability basis and deliberately
does not average heterogeneous absolute host metrics. Storage-target identity is part of each
operating envelope's environment evidence and must not be stripped to force comparability.

## Performance budgets

This command does not create performance budgets. Its purpose is to produce the comparable real
measurements needed before a later #440 budget decision can be justified. The operating envelope
therefore remains `budget_status = "not-established"` until retained measurements support a
versioned regression/noise policy.

## Pressure and destructive experiments

The existing `platform-host-pressure-observe` command remains the read-only real-host pressure
observer. The reference-host campaign does not allocate memory merely to induce pressure, change
swap/zRAM/cgroup or kernel settings, or attempt OOM conditions. Controlled paging/OOM-containment
work remains a separate dedicated-host #440 profile.

## CI boundary

Ordinary pull-request CI may run only the tiny `smoke` campaign to prove packaging, orchestration,
hashing, storage-target identity and schema compatibility. The one-hour release soak and
release-sized `1/10/50/100` sweeps are manual/release-qualification evidence and should run on the
actual documented reference hosts whose behavior is being claimed.

## Schema

The campaign manifest is validated by:

`docs/schemas/benchmark-reference-host-campaign.v1.schema.json`
