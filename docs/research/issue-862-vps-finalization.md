# Issue #862 reference-host handoff to #829

The real reference-host/VPS measurement that was originally the final external gate for #862 is no longer required to complete #862. It is owned by #829, which is the platform-wide issue for production-shaped validation that cannot be established honestly on GitHub-hosted CI runners.

#862 remains authoritative for the reproducible repository-side storage evaluation, exact upstream/runtime provenance, S3/FileProvider contract evidence, restart/auth/concurrency behavior, TLS, upgrades, disaster recovery, candidate-specific multi-node evidence, deterministic capture tooling and the repository-side backend classifications.

#829 is authoritative for executing and retaining the host-specific measurements described below and for any later deployment-profile or promotion/demotion decision that depends on those measurements.

## Reference-host prerequisites

For a #829 object-storage reference-host campaign:

- use an immutable platform commit containing the #862 evidence/capture tooling;
- use an ordinary documented Linux reference host/VPS, not a GitHub-hosted runner;
- keep Docker, Python 3.12+, Git, `sha256sum`, `tar`, `curl`, OpenSSL and `/usr/bin/time` available as required by the capture harness;
- do not place production credentials in the evidence directory;
- do not substitute a backend tag with a different repository digest.

The authoritative runtime digests already exercised by the #862 CI storage campaign are recorded in `tests/evidence/issue_862/runtime_image_digests.json`.

## Capture under #829

From the repository root on the reference host, run:

```bash
scripts/benchmarks/run_issue862_storage_vps_capture.sh
```

The default output directory is `artifacts/issue862-storage-vps`. The capture must finish successfully for the local-filesystem baseline and all three object stores. Keep the raw files, generated summaries, `SHA256SUMS`, archive and archive SHA-256 together as #829 evidence.

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
- RustFS, Garage and SeaweedFS used the exact repository digests already exercised by the authoritative #862 CI campaign;
- `SHA256SUMS` matches the captured files;
- the final evidence archive matches its recorded SHA-256.

A matching image tag with a different repository digest is not equivalent evidence. Digest drift blocks the affected #829 host claim until the changed runtime is reviewed and the relevant repository-side evidence is rerun.

## Measurements owned by #829

Review and retain at minimum:

- idle resident memory for each object-store service;
- peak and median active resident memory;
- peak sampled CPU during the common workload;
- concurrent round-trip duration;
- residual data-root footprint;
- startup/readiness behavior and any operational failures observed during the campaign;
- host CPU count, memory and OS metadata captured in the manifest.

Do not compare local-process RSS as though it were resident object-store daemon memory. Do not interpret residual data-root bytes as storage amplification; the summary intentionally keeps those resource shapes separate.

## #862 final classifications

The repository-side #862 evaluation finalizes as:

- RustFS: `experimental_only`;
- Garage: `supported_optional`;
- SeaweedFS: `supported_optional` comparison baseline;
- local filesystem: valid no-object-store path.

These classifications do not claim a universal VPS resource envelope. #829 may later provide host-specific suitability evidence and may justify a separate promotion/demotion or deployment-profile recommendation. In particular, RustFS remains `experimental_only` until a separate evidence-backed promotion decision is made; #862 does not wait for such a future decision.

## Failure rules for #829 host evidence

Keep a host-specific claim provisional or reject/defer it when any of the following occurs and cannot be explained and reproduced safely:

- canonical workload failure;
- runtime repository-digest mismatch;
- evidence checksum/archive-integrity failure;
- resource use unsuitable for the intended host profile;
- startup/restart instability observed in the target environment;
- a newly discovered licensing, security or operational blocker.

Unknown or missing measurements remain unknown; they are not inferred from GitHub-hosted runner observations.

## Ownership summary

- #862: repository/CI evaluation, deterministic harnesses, final repository-side backend classification.
- #829: real reference-host/VPS execution, resource/operational measurements, retained host evidence and any later host-specific recommendation.

Refs #829 #862 #40 #799
