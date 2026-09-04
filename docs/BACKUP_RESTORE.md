# Backup, restore, and disaster recovery

This document defines the first production-shaped backup boundary for the single-node deployment profile introduced by issue #39. It is vendor-neutral and relocatable.

## Consistency model

Backup format v1 is **offline/quiesced**. All processes that can write to the deployment data root must be stopped before `create` is invoked, and the operator must pass `--quiesced`. This is a deliberate correctness boundary: the platform currently has independent SQLite, file, and workspace providers and therefore does not have a cross-provider transaction manager.

Within that boundary, each SQLite database is copied with the SQLite backup API and checked with `PRAGMA integrity_check`; durable files/workspaces are copied while writers remain stopped. The backup is first assembled under a `.partial` sibling directory, verified, and only then atomically renamed to its final directory name. A failed/incomplete assembly is not a healthy backup.

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

## Format

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

`schemas/backup-manifest-v1.schema.json` is the normative manifest schema. `manifest.json` records format/schema version, platform version/commit, SQLite migration/user versions, consistency mode, included components, every payload file's size and SHA-256, external dependencies, exclusions, and restore policy.

The directory form is intentional for v1: operators may subsequently encrypt/package it with their normal backup tooling without the platform pretending to provide encryption it does not implement.

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
```

Restore refuses an existing target directory. It verifies the entire manifest/payload before writing, restores through a sibling `.restore-partial` directory, invalidates persisted authentication sessions, recreates the executor directory empty, and then atomically publishes the restored data directory. A stale partial restore directory is deleted on retry.

## Post-restore recovery

After restoring:

1. re-provision secret-provider key material/credentials using the selected secret backend;
2. install any optional adapters/providers required by the deployment; unavailable optional adapters must not prevent canonical state from being inspected;
3. start the platform against the new data root;
4. workers/nodes must authenticate and register again; never recreate stale live reservations from the old host;
5. unresolved/in-flight Runs must use the kernel's existing `run.recovery_required` / reconciliation semantics rather than being assumed to still execute;
6. rebuild disposable indexes/caches;
7. run `platform health`, `platform doctor`, and a reference smoke before returning the deployment to normal use.

The initial service records the required unfinished-run policy but does **not yet automate the cross-runtime reconciliation step**. That remains an explicit part of issue #40 before the issue can be closed.

## Disaster-recovery scenarios

### Total control-plane host loss

Provision a replacement host, install a compatible platform version, restore to a new data root, recover secrets separately, start in controlled recovery, re-register workers, reconcile unfinished runs, then run health/smoke checks.

### Database corruption

Do not overwrite the damaged deployment in place. Restore the last verified backup to a clean data root and retain the damaged data for forensic/manual recovery.

### File-store loss

Restore the full backup set, not only `files/`, unless an operator has independently proven the retained databases and selected file snapshot share the same consistency boundary.

### Migration to a new server/provider

Use normal backup/verify/restore. Absolute source paths are not encoded as restore destinations; the target data root is operator-selected.

### Loss of one worker

Do not restore the control plane merely to recreate a worker. Replace/re-register the worker; stale reservations must expire/reconcile.

### Optional adapter unavailable

Restore canonical platform state first. Reinstall the adapter later or keep it disabled; provider-specific external state is an explicitly declared dependency, not silently embedded in the generic archive.

### Interrupted restore

Rerun restore against the same clean target name. The stale `.restore-partial` sibling is removed before retry; a published target is never overwritten implicitly.

## RPO, RTO, frequency, and retention

There is no universal RPO/RTO promise. Backup frequency defines the maximum ordinary recovery-point window; data volume, verification, secret recovery, adapter installation, and reconciliation dominate recovery time.

A practical starting policy is daily verified backups plus a backup immediately before migrations/upgrades, with retention tiers chosen by the operator (for example daily/weekly/monthly). Retention deletion should be performed only after a newer backup has passed `platform-backup verify` and periodic restore drills have demonstrated usability. Store at least one copy outside the host being protected.

## Required verification before issue #40 can close

The v1 implementation covers round-trip relocation, checksum/missing-file detection, manifest compatibility, clean-target restore, interrupted-restore retry, session invalidation, secret-metadata rejection, symlink rejection, and ephemeral executor exclusion.

Still required for full issue acceptance: automated unfinished-run reconciliation after restore, explicit distributed worker re-registration/stale lease coverage against the distributed runtime, and an end-to-end restore smoke using the complete production deployment composition. Those should be added before marking #40 complete.
