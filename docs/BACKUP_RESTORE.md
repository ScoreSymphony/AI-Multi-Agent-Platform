# Backup, restore, and disaster recovery

This document defines the first production-shaped backup boundary for the single-node deployment profile introduced by issue #39. It is vendor-neutral and relocatable.

## Consistency model

Backup format v1 is **offline/quiesced**. All processes that can write to the deployment data root must be stopped before `create` is invoked, and the operator must pass `--quiesced`. This is a deliberate correctness boundary: the platform currently has independent SQLite, file, and workspace providers and therefore does not have a cross-provider transaction manager.

Within that boundary, each SQLite database is copied with the SQLite backup API and checked with `PRAGMA integrity_check`; durable files/workspaces are copied while writers remain stopped. SQLite WAL state is checkpointed into each snapshot and WAL/SHM/journal sidecars are not required by the resulting backup. The backup is first assembled under a `.partial` sibling directory, verified, and only then atomically renamed to its final directory name. A failed/incomplete assembly is not a healthy backup.

The single-node source must contain the durable `db/`, `files/`, and `workspaces/` components plus the canonical `db/kernel.sqlite3` anchor. A path that merely exists but does not have the required deployment layout is rejected rather than producing a formally valid but incomplete backup.

## V1 scope

Included:

- all SQLite state under `data_dir/db`, including canonical kernel/event history and the other configured single-node stores;
- non-SQLite durable state under `data_dir/db`, such as agent definitions;
- durable files under `data_dir/files`;
- durable workspace data under `data_dir/workspaces`;
- non-secret deployment metadata needed to identify the deployment profile;
- per-SQLite `PRAGMA user_version`, platform version/commit, checksums, exclusions, and restore policy in the manifest.

Excluded:

- `data_dir/executor`: execution workspaces are ephemeral and are recreated empty;
- disposable caches/indexes that can be rebuilt;
- live worker/node reservations, leases, and heartbeat state;
- plaintext secret material.

The current local secret provider is not a durable production secret backend. Secret-provider key material must therefore be backed up through the selected provider's protected mechanism, independently from the generic platform backup. Secret references/metadata may be restored; plaintext credentials must not be added to generic deployment metadata. The backup service rejects secret-looking metadata keys and refuses symlinks in the backup scope.

## Format and runtime verification

A backup is a directory:

```text
backup/
├── manifest.json
└── payload/
    ├── db/
    ├── files/
    ├── workspaces/
    └── metadata/deployment.json
```

`schemas/backup-manifest-v1.schema.json` is the normative manifest schema. The same schema is packaged with `ai_multi_agent_platform.backup` so an installed `platform-backup verify` validates the manifest at runtime rather than relying only on repository tests. Repository tests require the packaged schema and the normative schema to remain byte-equivalent as parsed JSON.

`manifest.json` records format/schema version, platform version/commit, SQLite migration/user versions, consistency mode, included components, every payload file's size and SHA-256, external dependencies, exclusions, and restore policy.

Verification is deliberately layered:

1. reject incompatible backup-format or manifest-schema versions;
2. validate the complete manifest against JSON Schema, including date-time format checks;
3. require the single-node component scope and canonical kernel entry;
4. verify that every manifest entry exists and has the declared size and SHA-256;
5. reject unmanifested payload files;
6. run `PRAGMA integrity_check` on every SQLite payload database;
7. require an exact one-to-one match between SQLite payload files and recorded `PRAGMA user_version` metadata;
8. reject secret-looking deployment metadata and a deployment profile other than `single-node`.

The directory form is intentional for v1: operators may subsequently encrypt/package it with their normal backup tooling without the platform pretending to provide encryption it does not implement.

## Compatibility boundary

V1 restore requires an exact platform release version by default through the installed `platform-backup` CLI. The manifest also records the source commit when supplied. Operators who need an exact-build restore can additionally pass `--expected-platform-commit`; a mismatch is rejected before any restore target is written.

The per-database `PRAGMA user_version` values are not treated as decorative metadata: backup verification compares them with the actual payload databases, and restore compares them again with the restored SQLite files before publishing the restored data root. Cross-version migrations and supported translation between different database revisions belong to #41 rather than being silently inferred by #40.

## Operator commands

The backup CLI is separate from the live API-first `platform` CLI because create/restore are offline host operations and must not depend on a running Control Plane.

```bash
# Stop the platform first.
platform-backup create \
  --data-dir /srv/ai-map/data \
  --destination /srv/backups/ai-map-2026-09-04 \
  --quiesced \
  --platform-commit <git-sha>

platform-backup verify /srv/backups/ai-map-2026-09-04

# Restore to a clean path; this can be on different compatible hardware.
platform-backup restore /srv/backups/ai-map-2026-09-04 \
  --target-data-dir /srv/ai-map-restored/data

# Optional exact-build pin in addition to the normal exact release-version check.
platform-backup restore /srv/backups/ai-map-2026-09-04 \
  --target-data-dir /srv/ai-map-restored/data \
  --expected-platform-commit <git-sha>
```

Restore refuses an existing target directory. It verifies the entire manifest/payload before writing, restores through a sibling `.restore-partial` directory, invalidates persisted authentication sessions, recreates the executor directory empty, re-runs durable-layout/SQLite-integrity/schema-version checks, writes a recovery-required marker, and then atomically publishes the restored data directory. A stale partial restore directory is deleted on retry.

## Post-restore recovery and readiness gate

A successful restore creates:

```text
data/
└── recovery/
    └── restore-pending.json
```

Normal `platform-server serve` checks restore recovery state **before the Control Plane starts listening**. Operators can perform the same complete recovery/readiness pass explicitly without starting the network server:

```bash
platform-server recover-restore
```

The recovery sequence is:

1. run the kernel's existing `recover_all()` over every canonical Task stream;
2. classify unresolved active Runs;
3. if no unresolved Run remains, verify the restored durable SQLite layout against the backup's recorded schema versions;
4. reconstruct every recovered Task and referenced Run and validate Task→Project, Run→Task/Step, and Workspace→Project relationships;
5. verify READY file metadata against durable file bytes, file size, SHA-256, and Project references;
6. run the composed Control Plane provider health probe and require `status=healthy` and `ready=true`;
7. write `recovery/restore-report.json` with the reconciliation result, validation checks and `ready_for_service` decision.

A report with `ready_for_service=false` is a persistent safe-mode gate. This remains true even after the one-shot `restore-pending.json` marker has been consumed. Both `platform-server serve` and `platform-server recover-restore` retry a blocked report on later invocations. Consequently, a failed validation or unresolved restored Run cannot be bypassed simply by restarting the server.

`platform-server serve` returns without starting Uvicorn when recovery remains blocked. A successful readiness pass records `ready_for_service=true`; only then may normal serving proceed.

The kernel's existing semantics remain authoritative:

- terminal Runs remain unchanged;
- queued Runs remain pending;
- recoverable starting Runs follow the existing retry-safe dispatch recovery path;
- RUNNING Runs whose former backend no longer exists remain canonically RUNNING but gain `run.recovery_required` with reason `canonical_running_backend_not_found`;
- no canonical RUNNING Run is blindly redispatched merely because the replacement host lacks the old execution process.

After restoring:

1. re-provision secret-provider key material/credentials using the selected secret backend;
2. install any optional adapters/providers required by the deployment; unavailable optional adapters do not prevent canonical state from being restored and inspected;
3. run `platform-server recover-restore` or start through `platform-server serve`; both use the same persistent readiness gate;
4. workers/nodes authenticate and register again; never recreate stale live reservations from the old host;
5. inspect unresolved Run IDs in `recovery/restore-report.json` and reconcile them through the normal kernel/operator flow;
6. rebuild disposable indexes/caches;
7. after the automated gate passes, run a reference smoke as an operational end-to-end confirmation before returning a migrated installation to users.

## Distributed runtime disaster recovery

Normal short-lived control-process restart and disaster restore are intentionally different operations. The existing `DistributedRegistry.restore_snapshot()` remains the restart-safe path and may retain capacity claims while all restored liveness starts offline.

For host/control-plane disaster recovery, pass the persisted registry snapshot through `prepare_registry_disaster_recovery(...)` before `restore_snapshot()`. The disaster transformation:

- preserves stable canonical Node and Worker IDs and non-ephemeral configuration;
- forces Nodes and Workers offline;
- resets heartbeat sequences;
- resets persisted Worker active-job counters;
- drops all active/reserved capacity claims.

A Worker becomes schedulable again only after a fresh `RegistrationRequest`/authentication path reports its current state. This prevents old reservations from being treated as proof that a lost process or machine is still executing work.

The fully packaged heterogeneous/multi-device deployment profiles remain owned by #240. #40 owns the durable-state relocation and disaster semantics they must consume: machine-local liveness/reservations are not canonical identity, durable IDs survive relocation, and replacement Workers must re-register. End-to-end tests involving different host/resource/device layouts should therefore be added alongside #240 without weakening the single-node recovery guarantees defined here.

## Disaster-recovery scenarios

### Total control-plane host loss

Provision a replacement host, install a compatible platform version, restore to a new data root, recover secrets separately, run post-restore recovery, re-register workers, inspect unresolved runs, then run the reference smoke.

### Database corruption

Do not overwrite the damaged deployment in place. Restore the last verified backup to a clean data root and retain the damaged data for forensic/manual recovery.

### File-store loss

Restore the full backup set, not only `files/`, unless an operator has independently proven the retained databases and selected file snapshot share the same consistency boundary.

### Migration to a new server/provider

Use normal backup/verify/restore. Absolute source paths are not encoded as restore destinations; the target data root is operator-selected. Machine-local hostnames and paths are not canonical IDs.

### Loss of one worker

Do not restore the control plane merely to recreate a worker. Replace/re-register the worker; stale reservations must expire/reconcile.

### Optional adapter unavailable

Restore canonical platform state first. Reinstall the adapter later or keep it disabled; provider-specific external state is an explicitly declared dependency, not silently embedded in the generic archive. Canonical history remains inspectable without requiring that optional runtime to be online during restore.

### Interrupted restore

Rerun restore against the same clean target name. The stale `.restore-partial` sibling is removed before retry; a published target is never overwritten implicitly.

## RPO, RTO, frequency, and retention

There is no universal RPO/RTO promise. Backup frequency defines the maximum ordinary recovery-point window; data volume, verification, secret recovery, adapter installation, and reconciliation dominate recovery time.

A practical starting policy is daily verified backups plus a backup immediately before migrations/upgrades, with retention tiers chosen by the operator (for example daily/weekly/monthly). Retention deletion should be performed only after a newer backup has passed `platform-backup verify` and periodic restore drills have demonstrated usability. Store at least one copy outside the host being protected.

## Verification coverage

The v1 implementation covers:

- full backup/restore relocation onto a different data root;
- canonical Task/Project/user ID preservation;
- SQLite WAL checkpoint/self-contained snapshot behavior;
- runtime JSON-Schema validation using the schema included in installed packages;
- required single-node scope and canonical-kernel completeness checks;
- checksum corruption and missing-file detection;
- SQLite `PRAGMA integrity_check` plus payload/manifest/restored `user_version` agreement;
- manifest/schema/platform compatibility rejection and optional exact-commit pinning;
- clean-target restore and interrupted-restore retry;
- authentication-session invalidation;
- secret-metadata rejection and symlink escape rejection;
- ephemeral executor exclusion;
- active-Run reconciliation after disaster restore;
- persistent blocked-report retry when unresolved Runs remain;
- post-restore canonical Task/Run/Project/Workspace reference validation;
- post-restore durable file metadata/byte/checksum validation;
- provider health/readiness gating before normal serving;
- distributed stale-reservation removal and Worker re-registration;
- restore while an optional adapter/runtime is unavailable;
- restored single-node reference smoke.

For #40 itself, heterogeneous topology packaging is not duplicated here: the remaining cross-device deployment-profile acceptance belongs to #240, while #40 defines and tests the durable relocation/recovery contract that those profiles must use.
