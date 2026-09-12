# Issue #862 target-VPS finalization gate

This note defines the final evidence procedure for #862. It does not replace the
full evaluation in `issue-862-object-storage-evaluation.md`; it makes the last
external measurement and classification step deterministic.

## Preconditions

Before a target-VPS result may finalize a storage classification:

- use the current `issue-862-rustfs-garage-storage-evaluation` branch head;
- use an ordinary Linux VPS, not a GitHub-hosted runner;
- keep Docker, Python 3.12+, Git, `sha256sum`, `tar`, `curl`, OpenSSL and
  `/usr/bin/time` available as required by the capture harness;
- do not place production credentials in the evidence directory;
- do not substitute a backend tag with a different repository digest.

The authoritative runtime digests already exercised by the CI storage campaign
are recorded in `tests/evidence/issue_862/runtime_image_digests.json`.

## Capture

From the repository root on the target VPS, run:

```bash
scripts/benchmarks/run_issue862_storage_vps_capture.sh
```

The default output directory is `artifacts/issue862-storage-vps`. The capture
must finish successfully for the local-filesystem baseline and all three object
stores. Keep the raw files, generated summaries, `SHA256SUMS`, archive and
archive SHA-256 together.

## Verify runtime identity and evidence integrity

Immediately after the capture, run:

```bash
python3 scripts/benchmarks/verify_issue862_storage_vps_capture.py \
  artifacts/issue862-storage-vps \
  | tee artifacts/issue862-storage-vps-verification.json
```

A passing result proves that:

- the local-filesystem and object-store workloads reported success;
- each object-store backend produced active resource samples;
- RustFS, Garage and SeaweedFS used the exact repository digests already
  exercised by the authoritative CI campaign;
- `SHA256SUMS` matches the captured files;
- the final evidence archive matches its recorded SHA-256.

A matching image tag with a different repository digest is **not** equivalent
evidence. Digest drift blocks final classification until the changed runtime is
reviewed and the relevant CI evidence is rerun.

## Interpret the measurement

Review at minimum:

- idle resident memory for each object-store service;
- peak and median active resident memory;
- peak sampled CPU during the common workload;
- concurrent round-trip duration;
- residual data-root footprint;
- startup/readiness behavior and any operational failures observed during the
  campaign;
- host CPU count, memory and OS metadata captured in the manifest.

Do not compare local-process RSS as though it were resident object-store daemon
memory. Do not interpret residual data-root bytes as storage amplification; the
summary intentionally keeps those resource shapes separate.

## Classification rules

### RustFS

The expected #862 outcome remains `experimental_only` unless new evidence
justifies a separate promotion decision. A successful VPS run is sufficient to
complete this evaluation while retaining `experimental_only`; #862 does not
need to remain open waiting for a future stable RustFS release.

### Garage

Garage may finalize as `supported_optional` when the target-VPS resource and
operational result is acceptable. Its AGPL-3.0 boundary remains explicit:
Garage is a separately deployed optional service behind the platform-owned
storage adapter, not incorporated into the MIT platform core.

### SeaweedFS

SeaweedFS remains the `supported_optional` comparison baseline when the same
VPS campaign is acceptable. It does not become mandatory or canonical.

### Local filesystem

Local filesystem remains the valid no-object-store path regardless of which
optional S3-compatible backends are supported.

## Failure rules

Use `reject/defer` or keep the existing classification provisional when any of
the following occurs and cannot be explained and reproduced safely:

- canonical workload failure;
- runtime repository-digest mismatch;
- evidence checksum/archive-integrity failure;
- resource use unsuitable for the intended VPS profile;
- startup/restart instability observed in the target environment;
- a newly discovered licensing, security or operational blocker.

Unknown or missing measurements remain unknown; they are not inferred from
GitHub-hosted runner observations.

## Final repository gate

After the target-VPS evidence is retained and classifications are updated:

1. update the #862 evaluation record and evidence manifest with the measured
   result and final classifications;
2. keep the exact target-VPS evidence location/hash in the PR record;
3. rerun normal repository CI and required checks on the final head;
4. only then move PR #863 out of draft and merge it.

The VPS measurement is the remaining external technical acceptance gate. All
#862 hard dependencies and the CI-backed S3, recovery, TLS, upgrade and
multi-node evidence are already complete on this branch.
