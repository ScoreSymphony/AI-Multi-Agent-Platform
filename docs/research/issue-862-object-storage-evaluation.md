# Issue #862 — RustFS / Garage object-storage evaluation

Status: **runtime evaluation complete; final classification still gated by real target-VPS measurements and final #799 reconciliation**. The CI-backed campaigns now pass the canonical S3/FileProvider contract, concurrency/integrity, authentication rejection, restart persistence, forward upgrades, TLS, clean-store disaster recovery and the candidate-specific multi-node failure scenarios. Garage's documented native SQLite metadata snapshot/restore path also passes. GitHub-hosted runner resource observations remain diagnostics only and do not satisfy the VPS acceptance criterion.

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

## Reviewed upstream snapshots

| Backend | Reviewed ref | Commit | License | Current role in #862 |
|---|---|---|---|---|
| RustFS | `1.0.0-rc.6-preview.1` | `5cd58319ed6148ed7f09f2a4d0b4e46e429f043a` | Apache-2.0 | candidate; provisional `experimental_only` |
| Garage | `v2.3.0` | `7b119c0b4fa58ab3cb6d5db435fe52d990f6a7aa` | AGPL-3.0 | candidate; provisional `supported_optional` |
| SeaweedFS | `4.46` | `d997fba1575583a89cf0cc50dc0150642286c86d` | Apache-2.0 | comparison baseline; provisional `supported_optional` |
| local filesystem | platform revision | platform revision | platform license | valid no-object-store path |

The classifications remain provisional because #862 explicitly requires target-VPS resource and operational measurements. CI evidence alone cannot close that gate.

## Source-backed findings

### RustFS

RustFS covers the platform-required S3 object surface, ranged reads, `ListObjectsV2` and the multipart primitives relevant to a future large-object path. The newest reviewed label, `1.0.0-rc.6-preview.1`, intentionally publishes no Docker image; the executable runtime candidate is `rustfs/rustfs:1.0.0-rc.6`. Both refs resolve to source commit `5cd58319ed6148ed7f09f2a4d0b4e46e429f043a`.

The reviewed redundant topology is MNMD, and the #862 campaign therefore uses four nodes rather than treating a smaller SNSD/non-redundant shape as equivalent. The technical campaign passes, but RustFS remains provisional `experimental_only` because the reviewed release line is still pre-release/release-candidate and VPS/maturity evidence is intentionally a separate promotion gate.

TLS is tested through the native `RUSTFS_TLS_PATH` deployment boundary with certificate verification. Upstream rollback is version-specific rather than a universal downgrade contract; #862 therefore does not claim arbitrary new-version-to-old-binary safety.

### Garage

Garage v2.3.0 is a stable AGPL-3.0 S3-compatible object store aimed at small self-hosted and geo-distributed deployments. The license boundary remains explicit: Garage is evaluated as a separately deployed optional network service behind the platform-owned storage adapter, not as code incorporated into the MIT platform core. Operators remain responsible for obligations applying to the Garage program or modified Garage deployments.

For replication factor `3` in the default `consistent` mode, the live campaign confirms operation after product failure detection with one of three zones/nodes unavailable. The test does not lower consistency to `degraded` or `dangerous` to manufacture a pass.

Garage's documented TLS deployment path places a reverse proxy in front of the S3 endpoint; the TLS campaign follows that architecture. Garage's native SQLite metadata recovery path is also exercised using a metadata snapshot, replacement of the stopped database, restart and full table repair. A generic binary downgrade is not treated as supported merely because forward upgrade succeeds.

### SeaweedFS baseline

SeaweedFS 4.46 remains the comparison baseline. Its mini mode exposes the S3 gateway while the wider architecture separates master, volume, filer and S3 roles. The pinned 4.46 implementation exposes native HTTPS for S3, and the certificate-verified TLS campaign now passes. The baseline also passes the same clean-store logical blob recovery exercise used for the candidates.

## Authoritative runtime evidence

The current authoritative CI-backed runs are:

| Evidence layer | Workflow run | Result |
|---|---:|---|
| Common S3 subset, FileProvider, auth, restart, Garage native snapshot restore | `34697375096` | pass |
| Forward upgrades | `34697375110` | pass |
| TLS / HTTPS | `34697375129` | pass |
| RustFS four-node MNMD failure | `34697375081` | pass |
| Garage three-node RF=3 failure | `34697375089` | pass |
| Clean-store disaster recovery | `34697375094` | pass |

### Common S3 and FileProvider boundary

RustFS, Garage and SeaweedFS all pass:

- `PutObject`, `GetObject`, `HeadObject`, `DeleteObject`;
- ranged reads;
- `ListObjectsV2` reconciliation behavior;
- eight concurrent write/read round trips with independent SHA-256;
- invalid S3 credential rejection;
- canonical `assert_file_provider_contract(...)` conformance;
- process/container restart followed by SHA-256 verification of an acknowledged object.

Earlier normalized raw evidence remains under `tests/evidence/issue_862/results/`; those records preserve pinned image identities/digests and the first identical raw workload. The current run table above is the authoritative pass/fail status.

## Forward-upgrade evidence

The pinned upgrade campaign verifies acknowledged SHA-256 content before and after an in-place runtime upgrade:

| Backend | Upgrade | Result |
|---|---|---|
| RustFS | `1.0.0-rc.5` → `1.0.0-rc.6` | pass |
| Garage | `v2.2.0` → `v2.3.0` | pass |
| SeaweedFS | `4.45` → `4.46` | pass |

A successful forward upgrade is **not** universal rollback evidence. RustFS has version-specific rollback floors; Garage recovery is documented around metadata snapshot/repair and backup procedures rather than a generic binary downgrade guarantee; SeaweedFS downgrade compatibility is likewise not assumed without version-specific upstream support.

## Distributed partial-failure evidence

### RustFS four-node MNMD

The four-node RustFS MNMD campaign passes with `1.0.0-rc.6`. Before failure, the cluster passes the common object workload. After one node is stopped, an acknowledged pre-failure object remains readable with the expected SHA-256 and a new object can be written/read with its expected SHA-256. After the node returns, both objects are verified again.

### Garage three-node replication factor 3

The corrected Garage workflow deploys three v2.3.0 nodes in three zones with replication factor 3 and default `consistent` mode, waits until `garage status` classifies the stopped node under `FAILED NODES`, and then verifies both existing reads and new writes. After the node returns and leaves the failed-node set, both objects are verified again. The authoritative corrected run passes.

The earlier timeout before Garage completed failure detection is retained as a harness lesson, not as product-failure evidence.

## Security / credentials / TLS

Synthetic CI credentials are generated at runtime, masked in Actions output and excluded from evidence JSON. Setup/profile state continues to contain component/configuration references only; credentials remain behind #34.

All three backends reject invalid credentials and pass certificate-verified HTTPS S3 workloads:

- RustFS: native TLS through `RUSTFS_TLS_PATH`;
- Garage: HTTPS through the documented reverse-proxy boundary;
- SeaweedFS: native S3 HTTPS flags in the pinned 4.46 runtime.

## Backup / restore boundary

### Important #40 limitation

The current #40 V1 single-node backup is a local-data-root backup. It covers `db/`, `files/`, `workspaces/` and configuration metadata; it does **not** automatically enumerate/copy opaque blob content from an external S3 endpoint.

An S3-backed deployment therefore has two coordinated recovery authorities:

1. canonical platform metadata/state through #40; and
2. object-store blob content through a separately verified object-store backup/recovery procedure.

#862 does not claim that the existing single-node backup already coordinates those two layers.

### Garage native metadata snapshot

Garage's native metadata recovery campaign now passes. It creates `garage meta snapshot --all`, mutates state after the snapshot, stops Garage, replaces the active SQLite metadata database with the selected native snapshot according to the documented recovery procedure, restarts Garage, performs full table repair, then verifies the restored pre-snapshot state.

This is metadata recovery, not full blob-store-loss recovery.

### Clean-store logical blob disaster recovery

RustFS, Garage and SeaweedFS all pass the same destructive storage-layer recovery campaign:

1. write deterministic blobs and verify SHA-256;
2. export them to a host-side logical backup with object key, byte count and SHA-256 but no credentials;
3. destroy the original object-store container and all backend data volumes;
4. create new empty volumes and a clean replacement backend;
5. verify that the old prefix is absent before restore;
6. restore every blob;
7. read and independently SHA-256-verify every restored object and listing.

This proves the storage-layer recovery mechanism only; it deliberately does not imply that #40 already invokes it automatically.

## VPS resource evidence

The repository contains `scripts/benchmarks/run_issue862_storage_vps_capture.sh`. It compares the same larger workload across local filesystem, RustFS `1.0.0-rc.6`, Garage `v2.3.0` and SeaweedFS `4.46`, capturing host identity, image digests, idle/active container stats, disk usage, workload output and a SHA-256 evidence manifest.

The script explicitly refuses to label a GitHub-hosted Actions execution as ordinary VPS evidence. This is intentional: #862 requires a real target-VPS measurement, and runner diagnostics cannot substitute for it.

**This is now the only missing technical acceptance measurement.**

## #799 setup-wizard mapping

#799 / PR #858 defines the provider-neutral storage discovery/profile shape, including component lifecycle classification, reversible profiles and exclusion of experimental components from automatic defaults.

The intended mapping is:

- #862 `experimental_only` → `ComponentLifecycle.EXPERIMENTAL`;
- #862 `supported_optional` → `ComponentLifecycle.SUPPORTED`.

This does not make any object-store product canonical. Local filesystem remains the no-object-store baseline, while an S3 adapter remains the product-neutral seam. Credentials are not written into setup profiles.

PR #858 is still open, so #862 records the mapping but does not import or hard-code the draft implementation as a stable dependency.

## Current decision table

| Backend | S3 + FileProvider | Restart | Auth | Forward upgrade | Multi-node failure | TLS | Store-loss restore | VPS measurement | Provisional outcome |
|---|---|---|---|---|---|---|---|---|---|
| RustFS | pass | pass | pass | pass | pass, 4-node MNMD | pass, native | pass | pending | `experimental_only` |
| Garage | pass | pass | pass | pass | pass, 3-node RF=3 | pass, reverse proxy | pass; native metadata restore pass | pending | `supported_optional` candidate |
| SeaweedFS | pass | pass | pass | pass | comparison baseline | pass, native | pass | pending | `supported_optional` baseline |
| local filesystem | canonical local path | platform-owned | platform-owned | platform-owned | n/a | deployment boundary | #40 local backup path | pending same campaign | valid no-object-store path |

## Remaining gates

All reproducible CI-backed runtime gates required by #862 are now complete. The remaining gates are deliberately external to ordinary GitHub-hosted CI:

1. execute the prepared local-filesystem/RustFS/Garage/SeaweedFS resource campaign on the actual target VPS environment and record the resulting evidence manifest;
2. keep RustFS at `experimental_only` unless that VPS/maturity review supports a stronger classification and the upstream release line has matured sufficiently;
3. reconcile the final classification with #799 once PR #858 is canonical, without duplicating its provider/profile persistence or secret handling;
4. require the normal repository CI/required checks to be green on the final documentation/evidence head before merge.

Until those gates close, PR #863 remains draft and no classification is marked final.
