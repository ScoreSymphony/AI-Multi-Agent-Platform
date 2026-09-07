# Backup, restore, and disaster recovery

This document defines the production-shaped backup boundary for the single-node deployment profile introduced by issue #39. It is vendor-neutral and relocatable.

## Consistency model

Backup format v1 is **offline/quiesced**. All processes that can write to the deployment data root must be stopped before `create` is invoked, and the operator must pass `--quiesced`. This is a deliberate correctness boundary: the platform currently has independent SQLite, file, and workspace providers and therefore does not have a cross-provider transaction manager.

Within that boundary, each SQLite database is copied with the SQLite backup API and checked with both `PRAGMA integrity_check` and `PRAGMA foreign_key_check`; durable files/workspaces are copied while writers remain stopped. SQLite WAL state is checkpointed into each snapshot and WAL/SHM/journal sidecars are not required by the resulting backup. The backup is first assembled under a `.partial` sibling directory, verified, and only then atomically renamed to its final directory name. A failed/incomplete assembly is not a healthy backup.

The single-node source must contain the durable `db/`, `files/`, and `workspaces/` components plus every store marked required by `ai_multi_agent_platform.backup.inventory`. The current initialized SQLite inventory is:

- `db/kernel.sqlite3`
- `db/scopes.sqlite3`
- `db/files.sqlite3`
- `db/workspaces.sqlite3`
- `db/verification.sqlite3`
- `db/authentication.sqlite3`
- `db/authorization.sqlite3`
- `db/automation.sqlite3`
- `db/notifications.sqlite3`

Lazy JSON stores remain inside the durable `db/` scope and are included whenever present. The authoritative inventory includes Agent, Conversation, model/provider, onboarding-command and Template repositories; in particular `db/templates.json` is durable platform state rather than an incidental file. A path that merely exists but does not have the required deployment layout is rejected rather than producing a formally valid but incomplete backup.

## V1 scope

Included:

- all SQLite state under `data_dir/db`, including canonical kernel/event history and the other configured single-node stores;
- non-SQLite durable state under `data_dir/db`, including Agent, Conversation, model, onboarding and Template stores when present;
- durable Notification inbox/preferences/delivery/projection state in `db/notifications.sqlite3`;
- durable files under `data_dir/files`;
- durable workspace data under `data_dir/workspaces`;
- non-secret deployment metadata needed to identify the deployment profile;
- per-SQLite `PRAGMA user_version`, platform version/commit, checksums, exclusions, restore policy and structured external-dependency metadata in the manifest.

Excluded:

- `data_dir/executor`: execution workspaces are ephemeral and are recreated empty;
- disposable caches/indexes that can be rebuilt;
- live worker/node reservations, leases, and heartbeat state;
- plaintext secret material.

The current local secret provider is not a durable production secret backend. Secret-provider key material must therefore be backed up through the selected provider's protected mechanism, independently from the generic platform backup. Secret references/metadata may be restored; plaintext credentials must not be added to generic deployment metadata. The backup service rejects secret-looking metadata keys and refuses symlinks in the backup scope.

## Structured external dependencies

New v1 backups record actual recoverable dependencies as structured manifest entries instead of generic prose. Each entry identifies:

- a stable `dependency_id` and `kind`;
- whether the feature dependency is required;
- whether its absence blocks restoration of canonical state;
- the durable metadata source from which it was discovered;
- the operator recovery action;
- value-safe identifying metadata.

The current single-node discovery path reads persisted model-provider setup records and their value-free `SecretReference` metadata, plus explicitly declared optional adapter runtimes. Provider endpoints and credential values are not copied into this inventory. A configured model provider may therefore be marked required for model execution while `restore_blocking=false`: canonical state remains recoverable and inspectable even if that runtime is temporarily unavailable. Secret-provider entries likewise describe which protected backend must be re-provisioned without copying `secret_id` values or plaintext material into the generic dependency inventory.

The v1 JSON Schema continues to accept the legacy string form so existing v1 backups remain verifiable. Newly created backups emit the structured form.

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

`manifest.json` records format/schema version, platform version/commit, SQLite migration/user versions, consistency mode, included components, every payload file's size and SHA-256, structured external dependencies, exclusions, and restore policy.

Verification is deliberately layered:

1. reject incompatible backup-format or manifest-schema versions;
2. validate the complete manifest against JSON Schema, including date-time format checks;
3. require the complete initialized single-node durable-store inventory;
4. verify that every manifest entry exists and has the declared size and SHA-256;
5. reject unmanifested payload files;
6. run `PRAGMA integrity_check` and `PRAGMA foreign_key_check` on every SQLite payload database;
7. require an exact one-to-one match between SQLite payload files and recorded `PRAGMA user_version` metadata;
8. reject secret-looking deployment metadata and a deployment profile other than `single-node`.

The directory form is intentional for v1: operators may subsequently encrypt/package it with their normal backup tooling without the platform pretending to provide encryption it does not implement.

## Compatibility boundary

V1 restore requires an exact platform release version through the installed `platform-backup` CLI. New operator-created backups also require an exact build commit. The CLI resolves that commit, in order, from an explicit `--platform-commit`/`--expected-platform-commit`, `AI_MULTI_AGENT_PLATFORM_BUILD_COMMIT`, or the current Git checkout. This prevents two materially different development builds that both report the package version `0.0.1` from being treated as equivalent merely because their release string matches.

A pinned backup is restored only when the running build commit matches. Older v1 backups whose historical manifest contains `platform.commit=null` remain schema-valid, but the operator CLI refuses to restore them silently. A trusted legacy backup requires the explicit `--allow-unpinned-backup` opt-in. This preserves format-v1 readability while keeping new disaster-recovery operations deterministic.

The per-database `PRAGMA user_version` values are not treated as decorative metadata: backup verification compares them with the actual payload databases, and restore compares them again with the restored SQLite files before publishing the restored data root. Cross-version migrations and supported translation between different database revisions belong to #41 rather than being silently inferred by #40.

## Operator commands

The backup CLI is separate from the live API-first `platform` CLI because create/restore are offline host operations and must not depend on a running Control Plane.

```bash
# Stop the platform first. In a Git checkout the commit is auto-detected.
platform-backup create \
  --data-dir /srv/ai-map/data \
  --destination /srv/backups/ai-map-2026-09-04 \
  --quiesced

# Packaged deployments can provide the immutable build commit through the environment.
export AI_MULTI_AGENT_PLATFORM_BUILD_COMMIT=<git-sha>

# An explicit pin remains available and overrides auto-detection.
platform-backup create \
  --data-dir /srv/ai-map/data \
  --destination /srv/backups/ai-map-2026-09-04 \
  --quiesced \
  --platform-commit <git-sha>

platform-backup verify /srv/backups/ai-map-2026-09-04

# Restore to a clean path; pinned backups require the same running build commit.
platform-backup restore /srv/backups/ai-map-2026-09-04 \
  --target-data-dir /srv/ai-map-restored/data

# Explicit current-build pin when auto-detection/environment is unavailable.
platform-backup restore /srv/backups/ai-map-2026-09-04 \
  --target-data-dir /srv/ai-map-restored/data \
  --expected-platform-commit <git-sha>

# Trusted legacy v1 backups with platform.commit=null require explicit acceptance.
platform-backup restore /srv/backups/legacy-v1 \
  --target-data-dir /srv/ai-map-restored/data \
  --allow-unpinned-backup
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
4. reconstruct every recovered Task and referenced Run and validate Task→Project and Run→Task/Step relationships;
5. verify READY file metadata against durable file bytes, file size, SHA-256, and Project references;
6. validate Agent/Team/AgentRun and Conversation/Message canonical references;
7. validate the actual `SqliteWorkspaceProvider` state, including Workspace→Project, head/base/parent snapshots, snapshot File references/checksums, Artifact references and snapshot-source references;
8. validate cross-store Authorization policy, Automation, Authentication credential-owner and Verification references wherever the current single-node composition owns the corresponding canonical registry;
9. validate durable Notifications, Notification preferences, delivery attempts and processed-event cursor references against current canonical Tasks/Runs/Projects/Workspaces/Automations/Verifications/Events and other owned registries;
10. reconstruct `db/templates.json` when present and validate Template owners/scopes, Template dependency/provenance revision references and instantiated canonical resource references;
11. run the composed Control Plane provider health probe and require `status=healthy` and `ready=true`;
12. write `recovery/restore-report.json` with the reconciliation result, validation checks and `ready_for_service` decision.

The cross-store gate deliberately does not invent authority for identities or resources that the current single-node composition does not own. For example, opaque service/worker identities are not rejected merely because there is no single-node service/worker registry, and optional runtime-provider availability is not confused with integrity of persisted canonical model configuration.

A report with `ready_for_service=false` is a persistent safe-mode gate. This remains true even after the one-shot `restore-pending.json` marker has been consumed. Both `platform-server serve` and `platform-server recover-restore` retry a blocked report on later invocations. Consequently, a failed validation or unresolved restored Run cannot be bypassed simply by restarting the server.

`platform-server serve` returns without starting Uvicorn when recovery remains blocked. A successful readiness pass records `ready_for_service=true`; only then may normal serving proceed.

The kernel's existing semantics remain authoritative:

- terminal Runs remain unchanged;
- queued Runs remain pending;
- recoverable starting Runs follow the existing retry-safe dispatch recovery path;
- RUNNING Runs whose former backend no longer exists remain canonically RUNNING but gain `run.recovery_required` with reason `canonical_running_backend_not_found`;
- no canonical RUNNING Run is blindly redispatched merely because the replacement host lacks the old execution process.

### Resolving an orphaned restored Run

An orphaned restored `RUNNING` Run is intentionally not guessed to have succeeded. It keeps the deployment blocked until an operator records a canonical failure or cancellation. Use the offline recovery command shown below; there is deliberately no force-success option.

```bash
platform-server resolve-restore-run \
  --task-id <task_id> \
  --run-id <run_id> \
  --resolution failed \
  --reason "execution backend was lost with the original host"
```

`--resolution cancelled` is also supported when cancellation is the correct canonical outcome.

The command is accepted only when `recovery/restore-report.json` is currently non-ready, lists that exact Run in `unresolved_run_ids`, and records the exact Task/Run pair as `orphaned_reconciliation_required`. The terminal transition goes through the normal kernel `record_run_outcome()` path with a deterministic idempotency key. The complete reconciliation/integrity/readiness gate then runs again automatically. Arbitrary Runs cannot be terminalized through this recovery command.

After restoring:

1. inspect the manifest's structured external dependency inventory;
2. re-provision secret-provider key material/credentials using each selected secret backend's protected recovery mechanism;
3. reinstall/reconnect optional adapters/providers when their features are required; unavailable optional runtimes do not prevent canonical state from being restored and inspected;
4. run `platform-server recover-restore` or start through `platform-server serve`; both use the same persistent readiness gate;
5. workers/nodes authenticate and register again; never recreate stale live reservations from the old host;
6. for every orphaned Run named by a blocked report, decide the correct canonical outcome and use `platform-server resolve-restore-run` with an explicit reason;
7. rebuild disposable indexes/caches;
8. after the automated gate passes, run a reference smoke as an operational end-to-end confirmation before returning a migrated installation to users.

## Distributed runtime disaster recovery

Normal short-lived control-process restart and disaster restore are intentionally different operations. The existing `DistributedRegistry.restore_snapshot()` remains the restart-safe path and may retain capacity claims while all restored liveness starts offline.

For host/control-plane disaster recovery, pass the persisted registry snapshot through `prepare_registry_disaster_recovery(...)` before `restore_snapshot()`. The disaster transformation:

- preserves stable canonical Node and Worker IDs and non-ephemeral configuration;
- forces Nodes and Workers offline;
- resets heartbeat sequences;
- resets persisted Worker active-job counters;
- drops all active/reserved capacity claims.

A Worker becomes schedulable again only after a fresh `RegistrationRequest`/authentication path reports its current state. This prevents old reservations from being treated as proof that a lost process or machine is still executing work.

The fully packaged heterogeneous/multi-device deployment profiles remain owned by #240. #40 owns the durable-state relocation and disaster semantics they must consume: machine-local liveness/reservations are not canonical identity, durable IDs survive relocation, and replacement Workers must re-register. #240 must exercise the #40 contract when it adds real multi-host reference profiles: restore the durable Control Plane state onto a replacement topology, reauthenticate/re-register replacement Workers, and prove that canonical Task/Run history remains unchanged while new work can execute on the replacement resources.

## Disaster-recovery scenarios

### Total control-plane host loss

Provision a replacement host, install a compatible platform version/build, restore to a new data root, recover protected dependencies/secrets separately, run post-restore recovery, resolve any orphaned Runs explicitly, re-register workers, then run the reference smoke.

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

- full backup/restore relocation onto a different clean data root;
- canonical user/Task/Run history preservation on the replacement installation;
- SQLite WAL checkpoint/self-contained snapshot behavior;
- runtime JSON-Schema validation using the schema included in installed packages;
- authoritative initialized durable-store completeness checks, including current Notification persistence and lazy Template persistence;
- checksum corruption and missing-file detection;
- SQLite `PRAGMA integrity_check`, `PRAGMA foreign_key_check` and payload/manifest/restored `user_version` agreement;
- manifest/schema/platform compatibility rejection, exact operator build provenance, and explicit legacy-unpinned restore opt-in;
- structured, non-secret external dependency inventory with backward-compatible v1 schema validation;
- clean-target restore and interrupted-restore retry;
- authentication-session invalidation;
- secret-metadata rejection and symlink escape rejection;
- ephemeral executor exclusion;
- active-Run reconciliation after disaster restore;
- persistent blocked-report retry when unresolved Runs remain;
- safe offline failure/cancellation resolution for Runs explicitly named as orphaned by the blocked restore report;
- post-restore Task/Run/Project/File/Agent/Team/Conversation reference validation;
- post-restore `SqliteWorkspaceProvider`, Authorization, Automation, Authentication-owner and Verification cross-store reference validation;
- post-restore Notification inbox/preference/delivery/event-cursor reference validation;
- post-restore Template owner/scope/dependency/provenance/instantiation reference validation;
- provider health/readiness gating before normal serving;
- distributed stale-reservation removal and Worker re-registration semantics;
- restore while an optional adapter/runtime is unavailable;
- restored single-node reference smoke.

For #40 itself, heterogeneous topology packaging is not duplicated here: the cross-device deployment-profile acceptance belongs to #240, which consumes this durable relocation/recovery contract. Cross-version upgrade/migration remains #41.
