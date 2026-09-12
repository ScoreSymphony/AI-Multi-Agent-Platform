# Issue #862 evidence harness

This directory contains the reproducible evidence harness for the RustFS / Garage object-storage evaluation.

## Evidence layers

The evaluation deliberately separates four evidence layers:

1. **Source/revision evidence** — exact upstream refs, commits, licenses and known compatibility limits.
2. **CI live-service evidence** — pinned containers exercised through the same S3 subset, SHA-256 checks, canonical `FileProvider` conformance, authentication rejection, concurrency and acknowledged-object restart recovery.
3. **Deployment evidence** — clean-store backup/restore, forward upgrade plus explicit rollback limits, TLS/auth deployment and multi-node partial-failure scenarios using deployment-shaped layouts.
4. **Reference-host evidence** — measured CPU, memory, disk footprint and operational behavior on a documented real VPS/reference host.

Layers 1-3 are the #862 completion evidence and are complete in CI. Layer 4 cannot be established honestly on GitHub-hosted runners, is therefore owned by #829, and does not block completion or repository-side classification in #862. GitHub-hosted runner resource observations remain diagnostic only and MUST NOT be promoted to reference-host sizing evidence.

## Canonical boundary

The platform remains the authority for File/Artifact identity, metadata, lifecycle and canonical SHA-256. Object stores are opaque content backends. Provider ETags are recorded only as provider observations and never become the canonical checksum.

The current object-operation subset is intentionally small:

- `PutObject`
- `HeadObject`
- `GetObject`, including ranged reads
- `ListObjectsV2` for repair/orphan reconciliation
- `DeleteObject`

Multipart upload is conditional on an adapter actually selecting a multipart large-object path. The canonical platform contract does not require ACLs, bucket versioning, Object Lock, provider lifecycle policies or ETag checksum semantics.

## Local probes

Export synthetic or deployment-scoped credentials through the environment; do not write them to evidence files:

```bash
export AWS_ACCESS_KEY_ID='...'
export AWS_SECRET_ACCESS_KEY='...'
```

Run the common subset probe against a prepared bucket:

```bash
python tests/evidence/issue_862/s3_contract_probe.py \
  --backend garage \
  --endpoint http://127.0.0.1:3900 \
  --bucket issue-862 \
  --region garage
```

Use `--create-bucket` only when the tested service is expected to allow the probe identity to create the bucket.

The restart probe is intentionally two-phase so the object is acknowledged before the storage process is restarted:

```bash
python tests/evidence/issue_862/s3_restart_probe.py write \
  --backend garage \
  --endpoint http://127.0.0.1:3900 \
  --bucket issue-862 \
  --region garage \
  --run-id restart-local

# Restart the storage service here.

python tests/evidence/issue_862/s3_restart_probe.py verify \
  --backend garage \
  --endpoint http://127.0.0.1:3900 \
  --bucket issue-862 \
  --region garage \
  --run-id restart-local
```

The verify phase recomputes the deterministic expected payload, validates SHA-256 after restart and deletes the sentinel only after successful verification.

## CI campaigns

The issue-specific workflows cover:

- the common S3/FileProvider/auth/restart contract for RustFS `1.0.0-rc.6`, Garage `v2.3.0` and SeaweedFS `4.46`;
- pinned forward upgrades from RustFS `rc.5`, Garage `v2.2.0` and SeaweedFS `4.45`;
- certificate-verified HTTPS through each backend's intended deployment boundary;
- RustFS four-node MNMD one-node failure;
- Garage three-node / three-zone replication-factor-3 one-node failure in default consistent mode;
- destructive clean-store logical blob backup/restore for all three backends;
- Garage's documented native SQLite metadata snapshot/restore plus table repair.

The authoritative passing run IDs are recorded in `storage_backends.json` and `docs/research/issue-862-object-storage-evaluation.md`. Synthetic credentials are generated only at runtime, masked in Actions output and excluded from evidence JSON.

## #799 reconciliation

#799 is complete and merged into `main`. The #862 branch includes that canonical component discovery/profile contract. The evidence mapping is regression-tested against the actual `ComponentLifecycle` enum:

- `experimental_only` -> `ComponentLifecycle.EXPERIMENTAL`
- `supported_optional` -> `ComponentLifecycle.SUPPORTED`

The mapping does not make an object-store product canonical, and credentials remain outside ordinary setup/profile persistence.

## Reference-host capture owned by #829

The capture tooling was implemented and regression-tested by #862, but execution and retention on a real host belongs to #829.

On the documented reference host/VPS run:

```bash
scripts/benchmarks/run_issue862_storage_vps_capture.sh
```

The script compares local filesystem, RustFS, Garage and SeaweedFS with the same larger workload, records host and image identity, idle/active resource samples, disk usage and workload timings, then creates a SHA-256 manifest and evidence archive. It refuses `GITHUB_ACTIONS=true` so hosted-runner numbers cannot be mislabeled as reference-host evidence.

It also invokes:

```bash
scripts/benchmarks/summarize_issue862_storage_vps_capture.py artifacts/issue862-storage-vps
```

The summarizer emits `storage-vps-summary.json` and `storage-vps-summary.md` while retaining explicit comparability guardrails. In particular, local-process RSS is not presented as equivalent to resident object-store daemon memory, and residual data-root bytes are not presented as storage amplification.

Verify the retained campaign with:

```bash
python3 scripts/benchmarks/verify_issue862_storage_vps_capture.py \
  artifacts/issue862-storage-vps
```

#829 owns the resulting host resource/operational evidence and any later host-specific deployment recommendation or promotion/demotion decision.

## Final #862 classifications

The reproducible repository-side evaluation is complete:

- RustFS: `experimental_only`;
- Garage: `supported_optional`;
- SeaweedFS: `supported_optional` comparison baseline;
- local filesystem: valid no-object-store path.

A future #829 reference-host campaign may add host-specific suitability evidence, but its absence does not make these #862 classifications provisional and does not block #862.

Refs #829 #862
