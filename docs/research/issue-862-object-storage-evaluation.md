# Issue #862 — RustFS / Garage object-storage evaluation

Status: **in progress**. Core S3/FileProvider compatibility, restart persistence, authentication rejection and forward-upgrade evidence are verified for all three live backends. RustFS additionally has successful four-node partial-failure evidence; Garage has successful HTTPS evidence through its documented reverse-proxy boundary. Clean-store disaster recovery, the corrected Garage three-node failure/recovery path, RustFS/SeaweedFS TLS completion and real target-VPS measurements remain open.

## Architectural boundary

The platform-owned `FileProvider` / Artifact contracts remain canonical. An S3 backend is only a blob-content transport/storage implementation; platform identity, artifact linkage, ownership, lifecycle state and the canonical SHA-256 remain in platform metadata. An S3 ETag MUST NOT be treated as the canonical checksum.

Local filesystem remains a valid deployment path. No object store is required or made canonical by this issue.

### Required S3 subset

For the current File/Artifact boundary the adapter should require only:

- `PutObject` — create/write object content;
- `GetObject` — full and ranged/streaming reads;
- `HeadObject` — existence/size/content metadata checks;
- `DeleteObject` — content deletion;
- `ListObjectsV2` — repair/orphan reconciliation, not canonical file listing.

Multipart upload (`CreateMultipartUpload`, `UploadPart`, `CompleteMultipartUpload`, `AbortMultipartUpload`) is conditional on the adapter's large-object path. ACLs, bucket versioning, Object Lock, provider lifecycle rules and ETag checksum semantics are not assumed by the canonical platform contract.

## Reviewed upstream snapshots

| Backend | Reviewed ref | Commit | License | Current role in #862 |
|---|---|---|---|---|
| RustFS | `1.0.0-rc.6-preview.1` | `5cd58319ed6148ed7f09f2a4d0b4e46e429f043a` | Apache-2.0 | candidate; provisional `experimental_only` |
| Garage | `v2.3.0` | `7b119c0b4fa58ab3cb6d5db435fe52d990f6a7aa` | AGPL-3.0 | candidate; provisional `supported_optional` |
| SeaweedFS | `4.46` | `d997fba1575583a89cf0cc50dc0150642286c86d` | Apache-2.0 | comparison baseline; provisional optional |
| local filesystem | platform revision | platform revision | platform license | valid no-object-store path |

The classifications above remain **provisional**. The issue explicitly requires target-VPS measurements, so GitHub-hosted CI evidence alone cannot make a classification final.

## Source-backed findings

### RustFS

RustFS publishes an S3 compatibility surface that covers the platform-required core operations, ranged reads, `ListObjectsV2` and multipart behavior while also identifying unsupported/planned features rather than claiming universal S3 compatibility. The newest reviewed label, `1.0.0-rc.6-preview.1`, intentionally has no published Docker image; the executable runtime candidate is `rustfs/rustfs:1.0.0-rc.6`. Both refs resolve to source commit `5cd58319ed6148ed7f09f2a4d0b4e46e429f043a`.

The reviewed production topology matters for classification: redundant MNMD operation requires a multi-node/multi-disk topology and the #862 campaign therefore tests a four-node cluster rather than pretending a two-node setup is equivalent. RustFS stays provisional `experimental_only` even after successful technical CI because the reviewed line is still a release-candidate/pre-release line and the issue requires operational/VPS maturity evidence before promotion.

TLS is configured through `RUSTFS_TLS_PATH`; the pinned source expects `rustfs_cert.pem`, `rustfs_key.pem` and may use `ca.crt` for strict verification. The TLS campaign uses those exact filenames and the runtime UID documented by the image configuration.

### Garage

Garage v2.3.0 is a stable AGPL-3.0 S3-compatible object store aimed at small self-hosted and geo-distributed deployments. The license boundary remains explicit: Garage is evaluated as a separately deployed optional network service behind the platform-owned storage adapter, not as code incorporated into the MIT platform core. Operators remain responsible for obligations applying to the Garage program or modified Garage deployments.

Garage's compatibility surface contains the platform-required object operations. Features such as complete AWS ACL/policy/versioning behavior are not required by the canonical platform boundary and are not silently assumed.

For replication factor `3` in the default `consistent` mode, Garage documents normal read/write availability when one node or one zone fails. The live failure workflow therefore waits until `garage status` actually classifies the stopped storage node under `FAILED NODES` before exercising degraded reads/writes. It does not lower consistency to `degraded` or `dangerous` merely to make the test pass.

Garage's documented TLS deployment path places a reverse proxy in front of the S3 endpoint. The CI campaign follows that architecture rather than inventing a native-TLS contract Garage does not publish for this path.

### SeaweedFS baseline

SeaweedFS 4.46 remains the comparison baseline. Its all-in-one/mini mode exposes the S3 gateway while the wider architecture separates master, volume, filer and S3 roles. The pinned 4.46 source exposes native S3 TLS flags (`s3.port.https`, `s3.cert.file`, `s3.key.file`, `s3.cacert.file`). Its operational surface is broader than local filesystem and Garage single-node modes, so resource/operational comparison still requires the same target-VPS campaign rather than relying on upstream claims.

## Verified live compatibility evidence

### Common S3 and FileProvider boundary

Workflow run `34658588799` verifies, for RustFS, Garage and SeaweedFS:

- the required S3 subset;
- ranged reads;
- `ListObjectsV2` reconciliation behavior;
- eight concurrent write/read round trips with independent SHA-256;
- invalid S3 credentials are rejected;
- the platform's canonical `assert_file_provider_contract(...)` conformance seam;
- a real process/container restart followed by SHA-256 verification of an acknowledged 1 MiB object.

RustFS and SeaweedFS completed that whole workflow successfully. Garage completed every common step successfully and failed only later in the first native metadata-snapshot enumeration attempt because the evidence workflow initially assumed a flat snapshot file. Garage actually created `snapshots/<timestamp>/db.sqlite`; the workflow now follows that documented/runtime-produced directory shape.

The earlier normalized raw results remain at:

- `tests/evidence/issue_862/results/rustfs-ci-34654695281.json`
- `tests/evidence/issue_862/results/garage-ci-34654695281.json`
- `tests/evidence/issue_862/results/seaweedfs-ci-34654695281.json`

Those earlier artifacts record the pinned image digests and first identical raw workload. The later workflow establishes canonical FileProvider conformance and authentication rejection in addition to that raw S3 evidence.

## Forward-upgrade evidence

Workflow run `34658588770` performed in-place upgrades against persistent backend data and verified the same acknowledged 1 MiB sentinel by independent SHA-256 before and after the upgrade.

| Backend | Upgrade | Result | SHA-256 preserved |
|---|---|---|---|
| RustFS | `1.0.0-rc.5` → `1.0.0-rc.6` | pass | yes |
| Garage | `v2.2.0` → `v2.3.0` | pass | yes |
| SeaweedFS | `4.45` → `4.46` | pass | yes |

Normalized evidence:

- `tests/evidence/issue_862/results/rustfs-upgrade-ci-34658588770.json`
- `tests/evidence/issue_862/results/garage-upgrade-ci-34658588770.json`
- `tests/evidence/issue_862/results/seaweedfs-upgrade-ci-34658588770.json`

Garage's v2.3.0 release states that it is stable and that migration from v2.2.0 has no breaking changes, which is consistent with the successful campaign.

**A successful forward upgrade is not rollback evidence.** No backend is marked downgrade-safe merely because old→new succeeded. The final report must retain an explicit unsupported/unknown rollback statement unless upstream support or a separate safe rollback campaign proves otherwise.

## Distributed partial-failure evidence

### RustFS four-node MNMD

Workflow run `34658588772` passed a four-node RustFS MNMD campaign using the pinned `1.0.0-rc.6` image.

Before failure, the cluster passed the full required S3 subset plus eight concurrent SHA-256 round trips. After one of four nodes was stopped, an acknowledged pre-failure object remained readable with the expected SHA-256 and a new object could be written and read with its expected SHA-256. After the node returned, both objects were verified again and deleted.

Normalized evidence: `tests/evidence/issue_862/results/rustfs-multinode-ci-34658588772.json`.

The per-container memory/CPU observations from this workflow are **GitHub-runner diagnostics only** and are not VPS sizing evidence.

### Garage three-node replication factor 3

The first three-node attempt began the S3 probe only three seconds after stopping one node. `garage status` still considered that node healthy at that instant, and the request timed out waiting for the unavailable peer. This is not treated as proof that replication-factor-3 failure handling is broken because the product had not yet completed failure detection.

The corrected workflow now:

1. deploys three Garage v2.3.0 nodes in three zones with replication factor 3 and default `consistent` mode;
2. writes and verifies an acknowledged object;
3. stops one storage node;
4. polls `garage status` until that exact node appears under `FAILED NODES`;
5. verifies the pre-failure object and a new write while the node is unavailable;
6. restarts the node and waits until it leaves the failed-node set;
7. verifies both objects again.

This campaign is still pending a successful corrected run and therefore is not yet counted as pass evidence.

## Security / credentials / TLS

Synthetic CI credentials are generated at runtime, masked in Actions output and excluded from evidence JSON. The platform setup/profile boundary continues to store component IDs/configuration references only; credentials belong behind #34 and are not persisted into #799 setup profiles.

Invalid credentials are rejected in the common live S3 campaign for all three backends.

Garage has additionally passed its certificate-verified HTTPS S3 subset and invalid-credential test through the documented Nginx reverse-proxy deployment shape. RustFS and SeaweedFS TLS campaigns have corrected pinned-runtime configurations and are awaiting authoritative successful runs; a previously cancelled SeaweedFS job is not counted as a product failure.

## Backup / restore boundary

### Important #40 limitation

The platform's current #40 V1 single-node backup implementation is explicitly a local-data-root backup. It snapshots/copies `db/`, `files/`, `workspaces/` plus configuration metadata. It **does not automatically enumerate and copy opaque blob data from an external S3 endpoint**.

Therefore enabling an S3-backed `FileProvider` requires coordinated recovery of two authorities:

1. canonical platform metadata/state through #40; and
2. object-store blob content through a separately verified object-store backup/recovery procedure.

#862 must not claim that the existing single-node backup automatically protects an external object store. A future platform backup hook may coordinate the two boundaries, but this issue does not redesign #40.

### Garage native metadata snapshot

Garage supports native metadata snapshots. The evidence campaign takes `garage meta snapshot --all`, mutates post-snapshot state, stops Garage, restores the generated `snapshots/<timestamp>/db.sqlite` into the active SQLite metadata file while retaining data blocks, starts Garage again and verifies the pre-snapshot state. This tests Garage metadata recovery, not full loss of the blob datastore.

The corrected snapshot-path campaign is pending an authoritative successful run.

### Clean-store logical blob disaster recovery

A separate provider-neutral campaign now tests full loss of the object-store data itself for RustFS, Garage and SeaweedFS:

1. write three deterministic synthetic blobs and verify SHA-256;
2. export them to a host-side logical backup with a manifest containing object key, byte count and SHA-256, but no credentials;
3. destroy the original object-store container and **all backend data volumes**;
4. recreate new empty volumes and a clean replacement backend;
5. require `ListObjectsV2` to prove the old test prefix is empty before restore;
6. restore every blob;
7. read and independently SHA-256-verify every restored object and the reconstructed listing.

This proves the storage-layer recovery mechanism only. It deliberately does not claim that #40 already invokes that mechanism automatically. The first authoritative matrix run is pending.

## VPS resource evidence

The repository now contains `scripts/benchmarks/run_issue862_storage_vps_capture.sh`. It compares the same larger workload across:

- local filesystem;
- RustFS `1.0.0-rc.6`;
- Garage `v2.3.0`;
- SeaweedFS `4.46`.

It captures host identity, image digests, idle and active container stats, disk usage, workload output and a SHA-256 evidence manifest. Like the existing #730 pattern, it explicitly refuses to run as `ordinary-VPS` evidence when `GITHUB_ACTIONS=true`.

No GitHub-hosted runner measurement may satisfy #862's VPS acceptance criterion. A real target-VPS capture is still required before final classification.

## #799 setup-wizard mapping

#799 / PR #858 now defines the provider-neutral storage discovery/profile shape. Its automatic recommendation path considers `RECOMMENDED`/`SUPPORTED` components and excludes `EXPERIMENTAL` components from automatic defaults.

The intended mapping, once #799 is canonical, is therefore:

- #862 `experimental_only` → `ComponentLifecycle.EXPERIMENTAL`;
- #862 `supported_optional` → `ComponentLifecycle.SUPPORTED`.

This mapping does not make a product canonical. Local filesystem remains the local baseline; an S3 adapter remains the product-neutral remote/object-store seam. #862 does not duplicate #799 profile persistence and does not write object-store credentials into those profiles.

Because #799 is not yet merged, #862 records this mapping but does not import or hard-code the draft implementation as a stable dependency.

## Current decision table

| Backend | S3 + FileProvider | Restart | Auth | Forward upgrade | Multi-node failure | TLS | Store-loss restore | VPS measurement | Provisional outcome |
|---|---|---|---|---|---|---|---|---|---|
| RustFS | pass | pass | pass | pass | pass, 4-node MNMD | pending corrected run | pending clean-store run | pending | `experimental_only` |
| Garage | pass | pass | pass | pass | pending corrected 3-node run | pass via reverse proxy | pending clean-store run; native metadata restore rerun pending | pending | candidate `supported_optional` |
| SeaweedFS | pass | pass | pass | pass | comparison baseline | pending authoritative run | pending clean-store run | pending | baseline optional |
| local filesystem | canonical local path | platform-owned | platform-owned | platform-owned | n/a | deployment boundary | #40 local backup path | pending same campaign | valid no-object-store path |

## Decision gates

A backend can become `supported_optional` only when all required platform operations pass and restart/recovery, backup/restore, security and upgrade evidence are reproducible. The final #862 classification additionally requires the real target-VPS resource/operational measurement required by the issue.

A useful backend with incomplete maturity or evidence remains `experimental_only`; a backend that cannot satisfy the required S3 subset or operational/security boundary becomes `reject/defer`.

The setup wizard may surface a supported backend only through #799's provider-neutral component lifecycle. It must not make any object-store product canonical and must not serialize credentials into ordinary profile/configuration state.

## Remaining gates

The remaining authoritative gates are now narrowly scoped:

1. corrected Garage three-node one-node-failure/recovery run;
2. corrected Garage native metadata-snapshot restore run;
3. RustFS and SeaweedFS certificate-verified TLS runs;
4. clean-store logical blob backup→total data-volume loss→restore matrix;
5. explicit rollback/downgrade support statements where upstream does not guarantee them;
6. target-VPS local-filesystem/RustFS/Garage/SeaweedFS resource campaign;
7. final #799 reconciliation after its provider lifecycle/profile contract merges.

Until those gates close, PR #863 remains draft and no classification is final.
