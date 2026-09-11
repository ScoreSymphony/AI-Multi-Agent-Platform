# Issue #862 evidence harness

This directory contains the reproducible evidence harness for the RustFS / Garage object-storage evaluation.

## Evidence layers

The evaluation deliberately separates four evidence layers:

1. **Source/revision evidence** — exact upstream refs, commits, licenses, known compatibility limits.
2. **CI live-service evidence** — pinned containers exercised through the same S3 subset, SHA-256 checks, concurrency workload and acknowledged-object restart recovery.
3. **Deployment evidence** — backup/restore, upgrade/rollback behavior, TLS/auth deployment and multi-node partial failure using deployment-shaped storage/layouts.
4. **VPS evidence** — measured CPU, RSS, disk overhead and operational behavior on the actual target VPS class.

A GitHub-hosted runner can satisfy layer 2 and can provide useful diagnostic resource observations, but its resource numbers MUST NOT be promoted to VPS sizing evidence.

## Canonical boundary

The platform remains the authority for File/Artifact identity, metadata, lifecycle and canonical SHA-256. Object stores are opaque content backends. Provider ETags are recorded only as provider observations and never become the canonical checksum.

The current object-operation subset is intentionally small:

- `PutObject`
- `HeadObject`
- `GetObject`, including a ranged read
- `ListObjectsV2` for repair/orphan reconciliation
- `DeleteObject`

Multipart upload is conditional on a future adapter actually selecting a multipart large-object path. The current canonical platform contract does not require ACL, bucket versioning, Object Lock or provider lifecycle policy support.

## Local probe

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

## CI campaign

`.github/workflows/issue-862-storage-evidence.yml` uses the same probe for:

- RustFS `rustfs/rustfs:1.0.0-rc.6`;
- Garage `dxflrs/garage:v2.3.0`;
- SeaweedFS `chrislusf/seaweedfs:4.46` as the comparison baseline.

RustFS source review also tracks the newer `1.0.0-rc.6-preview.1` label. The preview and published `1.0.0-rc.6` runtime tag resolve to the same reviewed commit; the preview tag itself intentionally has no published Docker image.

Each CI matrix leg uploads backend-specific evidence files containing the pinned image identity, S3 subset result, SHA-256 values, timings, post-workload Docker stats and both sides of the restart check. Synthetic credentials are passed only at runtime and are not serialized into those evidence files.

## Remaining gates before a final classification

A CI subset pass is necessary but not sufficient for `supported_optional`. The final backend decision still requires:

- backend-appropriate backup **and verified restore** evidence;
- upgrade evidence and an explicit statement about whether rollback/downgrade is supported;
- TLS/auth deployment evidence with credentials supplied through the platform secret boundary;
- multi-node partial-failure evidence for backends proposed for distributed deployment;
- measurements on the target VPS class;
- reconciliation with #799 before any setup-wizard recommendation is encoded.

Until those gates pass, classifications in `storage_backends.json` remain provisional.
