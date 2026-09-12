# Issue #862 — RustFS / Garage object-storage evaluation

Status: **repository-side evaluation complete; real reference-host/VPS measurements moved to #829**.

#862 owns the reproducible source, contract, CI, deployment-shaped and classification evidence for RustFS, Garage and the SeaweedFS comparison baseline. Measurements that require a real reference host/VPS are not inferred from GitHub-hosted runners and no longer block #862; their execution and retention are owned by #829.

## Architectural boundary

The platform-owned `FileProvider` / Artifact contracts remain canonical. An S3 backend is only a blob-content transport/storage implementation; platform identity, artifact linkage, ownership, lifecycle state and the canonical SHA-256 remain in platform metadata. An S3 ETag MUST NOT be treated as the canonical checksum.

Local filesystem remains a valid deployment path. No object store is required or made canonical by this issue.

### Required S3 subset

For the current File/Artifact boundary the adapter requires only:

- `PutObject` — create/write object content;
- `GetObject` — full and ranged/streaming reads;
- `HeadObject` — existence/size/content metadata checks;
- `DeleteObject` — content deletion;
- `ListObjectsV2` — repair/orphan reconciliation, not canonical file listing.

Multipart upload (`CreateMultipartUpload`, `UploadPart`, `CompleteMultipartUpload`, `AbortMultipartUpload`) remains conditional on the adapter's large-object path. ACLs, bucket versioning, Object Lock, provider lifecycle rules and ETag checksum semantics are not assumed by the canonical platform contract.

## Reviewed upstream snapshots and final #862 classifications

| Backend | Reviewed ref | Commit | License | Final #862 outcome |
|---|---|---|---|---|
| RustFS | `1.0.0-rc.6-preview.1` | `5cd58319ed6148ed7f09f2a4d0b4e46e429f043a` | Apache-2.0 | `experimental_only` |
| Garage | `v2.3.0` | `7b119c0b4fa58ab3cb6d5db435fe52d990f6a7aa` | AGPL-3.0 | `supported_optional` |
| SeaweedFS | `4.46` | `d997fba1575583a89cf0cc50dc0150642286c86d` | Apache-2.0 | `supported_optional` comparison baseline |
| local filesystem | platform revision | platform revision | platform license | valid no-object-store path |

RustFS remains `experimental_only` because the reviewed upstream is still on a pre-release/release-candidate line. A future promotion may use additional maturity and #829 reference-host evidence, but that future decision does not keep #862 open.

Garage is `supported_optional`. Its AGPL-3.0 boundary remains explicit: Garage is a separately deployed optional network service behind the platform-owned storage adapter, not code incorporated into the MIT platform core.

SeaweedFS remains the `supported_optional` comparison baseline and does not become canonical or mandatory.

## Repository-side runtime evidence

RustFS, Garage and SeaweedFS all pass the common platform workload covering:

- `PutObject`, `GetObject`, `HeadObject`, `DeleteObject`;
- ranged reads;
- `ListObjectsV2` reconciliation behavior;
- eight concurrent write/read round trips with independent SHA-256;
- invalid S3 credential rejection;
- canonical `assert_file_provider_contract(...)` conformance;
- process/container restart followed by SHA-256 verification of an acknowledged object.

The evaluated forward upgrades also pass:

| Backend | Upgrade | Result |
|---|---|---|
| RustFS | `1.0.0-rc.5` → `1.0.0-rc.6` | pass |
| Garage | `v2.2.0` → `v2.3.0` | pass |
| SeaweedFS | `4.45` → `4.46` | pass |

A successful forward upgrade is not universal rollback evidence. Version-specific upstream recovery/rollback constraints remain explicit.

## Distributed partial-failure evidence

### RustFS four-node MNMD

The four-node RustFS MNMD campaign passes with `1.0.0-rc.6`. After one node is stopped, an acknowledged pre-failure object remains readable with the expected SHA-256 and a new object can be written/read with its expected SHA-256. After the node returns, both objects are verified again.

### Garage three-node replication factor 3

The Garage campaign deploys three v2.3.0 nodes in three zones with replication factor 3 and default `consistent` mode, waits until the stopped node is classified as failed, verifies existing reads and new writes, then verifies both objects again after recovery.

## Security / credentials / TLS

Synthetic CI credentials are generated at runtime, masked in Actions output and excluded from evidence JSON. Setup/profile state contains component/configuration references only; credentials remain behind #34.

All three backends reject invalid credentials and pass certificate-verified HTTPS S3 workloads:

- RustFS: native TLS through `RUSTFS_TLS_PATH`;
- Garage: HTTPS through the documented reverse-proxy boundary;
- SeaweedFS: native S3 HTTPS in the pinned runtime.

## Backup / restore boundary

The current #40 V1 single-node backup is a local-data-root backup. It covers `db/`, `files/`, `workspaces/` and configuration metadata; it does not automatically enumerate/copy opaque blob content from an external S3 endpoint.

An S3-backed deployment therefore has two coordinated recovery authorities:

1. canonical platform metadata/state through #40; and
2. object-store blob content through a separately verified object-store backup/recovery procedure.

Garage's native SQLite metadata snapshot/restore path passes. RustFS, Garage and SeaweedFS also pass the same destructive clean-store logical blob recovery campaign: deterministic blobs are backed up with SHA-256 metadata, the original backend data is destroyed, a clean replacement store is created, and every restored object is independently verified.

## #799 setup-wizard reconciliation

#799 is complete and merged. The #862 branch uses the canonical provider-neutral setup/profile contract and maps classification vocabulary to the canonical lifecycle enum:

- `experimental_only` → `ComponentLifecycle.EXPERIMENTAL`;
- `supported_optional` → `ComponentLifecycle.SUPPORTED`.

This does not make any object-store product canonical. Local filesystem remains the no-object-store baseline, the S3 adapter remains the product-neutral seam, and setup-profile persistence continues to exclude secret material.

## Reference-host/VPS evidence moved to #829

The repository contains the deterministic capture, summarization and verification tooling prepared by #862:

- `scripts/benchmarks/run_issue862_storage_vps_capture.sh`;
- `scripts/benchmarks/summarize_issue862_storage_vps_capture.py`;
- `scripts/benchmarks/verify_issue862_storage_vps_capture.py`.

Those tools compare local filesystem, RustFS, Garage and SeaweedFS; capture host/runtime identity, resource observations, workload timing and disk state; pin exact runtime repository digests; reject stale evidence directories; verify evidence/archive hashes; and refuse GitHub-hosted Actions runs as ordinary reference-host evidence.

Execution on a real reference host/VPS and retention/interpretation of resource and operational measurements now belong to #829. The detailed handoff is recorded in `docs/research/issue-862-vps-finalization.md`.

A #829 campaign may later inform host-specific suitability, deployment-profile guidance, or a separate promotion/demotion decision. It does not retroactively make the #862 repository-side classifications provisional.

## Final decision table

| Backend | S3 + FileProvider | Restart | Auth | Forward upgrade | Multi-node failure | TLS | Store-loss restore | #829 reference-host follow-up | Final #862 outcome |
|---|---|---|---|---|---|---|---|---|---|
| RustFS | pass | pass | pass | pass | pass, 4-node MNMD | pass, native | pass | optional follow-up / promotion evidence | `experimental_only` |
| Garage | pass | pass | pass | pass | pass, 3-node RF=3 | pass, reverse proxy | pass; native metadata restore pass | host-specific suitability evidence | `supported_optional` |
| SeaweedFS | pass | pass | pass | pass | comparison baseline | pass, native | pass | comparison-host evidence | `supported_optional` baseline |
| local filesystem | canonical local path | platform-owned | platform-owned | platform-owned | n/a | deployment boundary | #40 local backup path | same host comparison | valid no-object-store path |

## Completion boundary

All #862 acceptance work is complete when the final branch head passes normal repository CI and the issue-specific storage workflows. Real reference-host/VPS resource measurements are explicitly excluded from the #862 completion gate and tracked by #829.

Refs #829 #862 #799 #40
