# Issue #40 cross-store integrity audit

A strict post-#302 audit found that the restore gate still proved SQLite integrity more broadly than semantic cross-store integrity. This follow-up closes that distinction without pulling #41 migrations or #240 distributed deployment packaging into #40.

## Added restore-integrity coverage

The single-node readiness gate now additionally validates:

- `SqliteWorkspaceProvider` Workspace→Project and head/base/parent Snapshot relationships;
- Workspace Snapshot File references against READY canonical File metadata and SHA-256 values;
- Workspace Snapshot aggregate content checksums and Artifact/source-Snapshot references;
- Authorization Project/Workspace scopes and canonical principals where the current composition owns the corresponding registry;
- Automation owner/principal, Project/Workspace, Task-template, delivery→Automation and generated-Task references;
- Authentication credential owners for canonical user/Automation/Agent identities that the current composition can resolve;
- Verification policy scopes, Task/Run/Project/Result/Artifact references, producer/verifier Agent/model references, evidence Artifacts and Task verification requirements.

Opaque service/worker/integration identities remain outside the SingleNode existence check where no canonical owner registry is composed. Optional provider runtime availability is likewise not treated as persisted-reference corruption.

## External dependency inventory

New backup-format-v1 manifests emit structured dependency records discovered from value-safe durable metadata. Current discovery includes persisted model-provider configurations, their SecretReference backend identities, and explicitly declared optional adapter runtimes. Plaintext secrets and secret IDs are not copied into the dependency inventory.

The v1 schema remains backward-compatible with legacy string dependency entries so previously created v1 backups remain verifiable.

## Operator runbook

`docs/BACKUP_RESTORE.md` now documents the post-#302 orphaned-Run operator flow explicitly through `platform-server resolve-restore-run`, including its blocked-report safety boundary, accepted terminal outcomes, deterministic idempotency and automatic readiness retry.

## Boundary with #240 and #41

#240 owns the real distributed/heterogeneous deployment profiles and must consume #40's durable relocation contract when those profiles are available: replacement Workers reauthenticate/re-register and canonical Task/Run history must survive topology relocation.

#41 owns cross-version platform/database migrations. #40 continues to guarantee deterministic recovery of compatible backup state rather than silently migrating incompatible schemas.
