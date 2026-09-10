# Cross-host operating-envelope catalog

Issue #440 requires retained measurements on documented reference environments before the project can make evidence-backed capacity or regression claims. The existing `platform-operating-envelope` analyzer deliberately combines only reports from the exact same environment. `platform-operating-envelope-catalog` adds the next layer: it catalogs independently tested host envelopes while keeping every host's absolute measurements separate.

## Why this is separate

Throughput and latency from different CPUs, memory sizes, kernels, storage devices or virtualized hosts are not interchangeable samples. Averaging them would create a number that describes no real deployment. The catalog therefore validates cross-host comparison basis and provenance, but performs no cross-host performance aggregation.

The output always states:

```text
claim_semantics = "per-host-tested-envelopes-only"
cross_host_aggregation = "not-performed"
budget_status = "not-established"
```

A host entry retains its environment metadata and fingerprint, tested concurrency levels, complete per-concurrency envelope, longest verified endurance duration, source path and a canonical SHA-256 digest of the source envelope.

## Comparable basis

Cataloged envelopes must share:

- platform version and exact platform commit;
- deployment profile;
- persistence profile;
- workload distribution;
- operations per sweep point;
- warmup operation count;
- timeout.

Environment metadata is intentionally allowed to differ. Concurrency level sets may also differ. The catalog records both their union and intersection so an operator can see which levels are actually available for direct cross-host inspection.

Each input must be a successful `single-node.reference.operating-envelope.analysis` v1 report with an environment fingerprint that still matches its environment metadata. Tampered or failed evidence is rejected.

## Comparison status

The catalog classifies the evidence set without inventing a capacity claim:

- `single-host`: only one host envelope was supplied;
- `same-environment-only`: multiple envelopes were supplied but all describe the same environment fingerprint;
- `cross-host-no-shared-concurrency`: distinct environments exist, but they have no common tested concurrency level;
- `cross-host-comparable`: at least two distinct environments exist and at least one concurrency level is shared.

`cross_host_comparison_ready=true` means only that the retained host evidence has a common basis and at least one common tested concurrency point. It does not mean the hosts have equal capacity and it does not establish a release budget.

## Usage

First generate a tested envelope independently on each reference host with the existing sweep/endurance tooling. Keep the exact platform commit and benchmark configuration aligned.

Then catalog the retained outputs:

```bash
platform-operating-envelope-catalog \
  --host vps-a=artifacts/reference/vps-a/operating-envelope.json \
  --host workstation-b=artifacts/reference/workstation-b/operating-envelope.json \
  --output artifacts/reference/cross-host-catalog.json
```

Labels are operator-facing evidence labels, not canonical Node or Worker identities.

## CI boundary

PR CI may package its one tiny operating-envelope fixture through this catalog to prove parsing, schema stability and provenance behavior. Such an output remains `single-host` and is not cross-host evidence.

Actual #440 cross-host completion evidence still requires independently executed and retained release-sized measurements on real documented reference environments. Those measurements should use the same commit and benchmark basis and should not be substituted with duplicated CI reports whose environment metadata was edited by hand.

## Budgets

This catalog intentionally does not derive global performance budgets. After multiple real reference-host measurement series exist, a later #440 slice can define versioned warning/release-blocking policies for explicitly named comparable environment classes or per-host baselines. Those policies must remain evidence-backed and must not convert the weakest, strongest or averaged host result into a universal hardware-independent promise.

## Schema

Catalog output is validated by:

`docs/schemas/benchmark-operating-envelope-catalog.v1.schema.json`
