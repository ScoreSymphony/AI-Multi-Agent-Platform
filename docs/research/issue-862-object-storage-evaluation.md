# Issue #862 — RustFS / Garage object-storage evaluation

Status: **in progress**. The research and evidence boundary is established; final runtime acceptance and setup-wizard integration remain gated by the campaigns below and by #799.

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
| SeaweedFS | `4.46` | `d997fba1575583a89cf0cc50dc0150642286c86d` | Apache-2.0 | comparison baseline; optional |
| local filesystem | platform revision | platform revision | platform license | mandatory no-object-store path |

The classifications above are deliberately **provisional**. They do not become final until the same platform workload has been exercised against each candidate/baseline where applicable.

## Source-backed findings

### RustFS

RustFS publishes an executable S3 compatibility matrix. The reviewed documentation explicitly covers common object operations, `ListObjects`/`ListObjectsV2`, ranged reads and core multipart upload, while also naming planned/excluded behavior instead of claiming universal S3 coverage. That is compatible with the platform's minimal-subset strategy, but the currently reviewed release line is still pre-release. TLS can be configured for both the S3 and console listeners. Because maturity, restart recovery, upgrades and resource use are explicit requirements of #862, RustFS stays provisional `experimental_only` until those campaigns pass.

The newest reviewed label, `1.0.0-rc.6-preview.1`, intentionally has no published Docker image. The executable CI candidate is therefore `rustfs/rustfs:1.0.0-rc.6`; both refs resolve to the same reviewed source commit `5cd58319ed6148ed7f09f2a4d0b4e46e429f043a`, so source review and runtime evidence remain revision-aligned without pretending the preview label itself is container-published.

### Garage

Garage v2.3.0 is an AGPL-3.0 S3-compatible object store targeted at small self-hosted and geo-distributed deployments. Its documented real-world deployment uses separate persistent metadata and data directories and a fixed image tag. This is attractive for a multi-VPS topology, but the platform must keep Garage an external optional service/adapter and document the AGPL boundary explicitly. Final support still depends on the platform's actual S3 subset, recovery, upgrade and resource campaigns.

Garage's documented compatibility surface includes the platform-required core object operations and multipart upload, while ACL/policy/versioning behavior is incomplete or absent. That is acceptable only because those features are explicitly outside the canonical platform requirement.

### SeaweedFS baseline

SeaweedFS 4.46 remains the comparison baseline. Its S3 service is a stateless gateway in front of the filer; master, volume, filer and S3 roles make its topology more operationally involved than a local-filesystem deployment. It provides replication/erasure-coding and documented backup/replication paths, so it is useful as the mature distributed baseline rather than as a mandatory platform dependency.

## Live evidence collected so far

Workflow run `34654695281` exercised the same dependency-free SigV4 probe against pinned single-node containers. Credentials were generated for the run and were not serialized into the evidence artifacts.

| Backend | Raw S3 subset | 8-way concurrent round trip | acknowledged object after process restart | GitHub-runner memory snapshot | Result scope |
|---|---|---|---|---:|---|
| Garage `v2.3.0` | pass | pass | pass | 5.93 MiB | single-node CI evidence only |
| SeaweedFS `4.46` | pass | pass | pass | 137 MiB | single-node CI baseline only |
| RustFS `1.0.0-rc.6` | pending in this run | pending | pending | pending | no result yet |

The resource snapshots above are diagnostics from a GitHub-hosted runner and **are not VPS sizing evidence**. They must not be used as the final resource comparison requested by #862.

### Verified Garage evidence

The Garage artifact records successful `PutObject`, `HeadObject`, full `GetObject`, ranged `GetObject`, `ListObjectsV2`, `DeleteObject` and eight concurrent write/read round trips with independent SHA-256 verification. A 1 MiB acknowledged restart sentinel had SHA-256 `e520d57071576ddcf7ee5f038f977c12ef50e575c3cfef21d815e425cfcaf8cb` both before and after a real container restart. The pinned image digest was `dxflrs/garage@sha256:866bd13ed2038ba7e7190e840482bc27234c4afaf77be8cfa439ae088c1e4690`.

The normalized evidence is committed at `tests/evidence/issue_862/results/garage-ci-34654695281.json`.

### Verified SeaweedFS baseline evidence

The SeaweedFS artifact passed the same raw S3 operations and eight concurrent round trips. Its 1 MiB restart sentinel retained SHA-256 `da61ea71ca4c706ea437b4751836b6c286badf69ae2dfcd84fe247edd1f536f4` across restart. The pinned image digest was `chrislusf/seaweedfs@sha256:08d516132314207d10c8e37cbffc1f32b147d870169688734cc61c6231625b62`.

The normalized evidence is committed at `tests/evidence/issue_862/results/seaweedfs-ci-34654695281.json`.

These results validate only the raw storage subset and single-process restart persistence. A newer workflow revision additionally runs the platform's existing `assert_file_provider_contract(...)` helper through a minimal test adapter so the final evidence proves the canonical provider seam instead of only proving raw S3 calls.

## Reproducible evidence plan

All candidates must be tested with the same object keys, payload corpus and assertions. The campaign MUST record command/configuration, upstream image/version digest where available, host CPU/RAM/disk, elapsed time and resulting checksums.

1. **Contract/S3 subset:** create, head, read, ranged/stream read, list-for-reconciliation and delete. Verify canonical SHA-256 independently of provider ETag.
2. **Concurrency/integrity:** parallel writers to distinct keys and concurrent readers; verify exact byte counts and SHA-256 after completion.
3. **Restart/failure:** restart object-store processes during/after writes, then validate acknowledged objects. Multi-node candidates additionally lose one node at a time according to their supported topology.
4. **Backup/restore:** back up according to the backend's supported procedure, rebuild a clean service, restore, then replay the platform checksum/orphan scan.
5. **Upgrade/rollback:** fixed source version A -> reviewed version B; validate before/after checksums and document whether downgrade is officially supported. Never infer rollback safety from successful upgrade alone.
6. **Security:** TLS enabled; credentials supplied only through #34 secret references/runtime environment. No access/secret key may be serialized into setup profiles or evidence fixtures.
7. **VPS resources:** capture idle and active RSS/CPU plus disk overhead for the same workload. Measurements, not upstream marketing numbers, decide VPS suitability.

## Decision gates

A backend can become `supported_optional` only when all required platform operations pass and restart/recovery, backup/restore, security and upgrade evidence are reproducible. A backend with useful functionality but incomplete maturity/evidence remains `experimental_only`; a backend that cannot satisfy the required S3 subset or operational/security boundary becomes `reject/defer`.

The setup wizard (#799) may recommend a supported backend only after these gates are complete. Until #799's discovery/profile contract is finalized, #862 MUST NOT hard-code wizard profile keys or persist object-store credentials.

## Current next evidence

The machine-readable source/revision/invariant manifest lives at `tests/evidence/issue_862/storage_backends.json`, with repository tests guarding the architecture boundary. The common harness lives under `tests/evidence/issue_862/` and the live workflow in `.github/workflows/issue-862-storage-evidence.yml`.

The next gates are: RustFS live evidence, canonical `FileProvider` conformance for all live backends, backend-appropriate backup plus verified restore, upgrade/rollback evidence, TLS/auth evidence, distributed partial-failure evidence, and target-VPS resource measurements. Final setup-wizard integration remains blocked on #799.
