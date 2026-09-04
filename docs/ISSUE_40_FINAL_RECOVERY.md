# Issue #40 final recovery/completeness follow-up

Issue #40 was reopened again after PR #278 because a strict audit against the issue text found three remaining single-node gaps. This document records the correction and keeps the remaining #240-dependent hardware-relocation proof explicit instead of treating it as already satisfied.

## Why the issue was reopened

PR #278 correctly added manifest-schema validation, SQLite integrity/schema checks, canonical Task/Run/Project/Workspace/file validation, and a persistent `ready_for_service` gate. However, three practical gaps remained:

1. an orphaned restored `RUNNING` Run could permanently block normal serving without an offline operator path to resolve it;
2. backup completeness required the durable component directories but only required `db/kernel.sqlite3` as a concrete durable store;
3. the restore-integrity gate did not have a composition boundary for additional durable application stores such as Agents and Conversations.

## Authoritative single-node durable-store inventory

`ai_multi_agent_platform.backup.inventory` now declares the durable stores owned by the current single-node composition.

Required initialized SQLite stores:

- `db/kernel.sqlite3`
- `db/scopes.sqlite3`
- `db/files.sqlite3`
- `db/workspaces.sqlite3`
- `db/verification.sqlite3`
- `db/authentication.sqlite3`
- `db/authorization.sqlite3`
- `db/automation.sqlite3`

Lazy stores are optional until created, but are inside the durable `db/` backup scope and therefore included whenever present:

- `db/agents.json`
- `db/conversations.json`
- `db/models.json`
- `db/model-providers.json`
- `db/onboarding-commands.json`

Backup creation and verification reject a source/backup that omits a required initialized store. SQLite verification also runs both `PRAGMA integrity_check` and `PRAGMA foreign_key_check`.

The inventory is intentionally separate from backup copy mechanics. When the single-node composition gains another durable store, that store must be added to the inventory with an explicit required/lazy decision instead of silently relying on recursive directory copying.

## Offline resolution for orphaned restored Runs

A disaster restore can legitimately find a canonical `RUNNING` Run whose original execution backend disappeared with the lost host. The kernel continues to mark this state `recovery_required` and the restore report remains `ready_for_service=false`.

The operator now has a narrow offline resolution command:

```bash
platform-server resolve-restore-run \
  --task-id <task_id> \
  --run-id <run_id> \
  --resolution failed \
  --reason "execution backend was lost with the original host"
```

`--resolution` accepts only `failed` or `cancelled`. There is deliberately no force-success option.

Safety rules:

1. `recovery/restore-report.json` must exist and be non-ready;
2. the exact Run ID must be listed in `unresolved_run_ids`;
3. the exact Task/Run pair must have disposition `orphaned_reconciliation_required` in that report;
4. the command uses the existing canonical kernel `record_run_outcome()` transition rather than editing persistence directly;
5. a deterministic idempotency key makes retry safe if the terminal transition succeeds but a later recovery step is interrupted;
6. after the transition, the complete post-restore reconciliation/integrity/readiness gate runs again automatically.

This removes the previous deadlock where normal serving was correctly blocked but no offline operator path could make progress.

## Composition-owned restore integrity

The generic backup integrity layer now accepts additional async restore validators. The concrete `SingleNodeDeployment` registers application-level validators without coupling backup core to every platform subsystem.

Current composed validators additionally verify:

- Agent/Agent Team project and workspace scopes;
- Agent and Team revision references;
- Agent Team member revision references;
- AgentRun Task/Run, Agent/Team revision, model configuration and attached Artifact/Result references;
- Conversation project/workspace references;
- Conversation Agent/Team participants and default selection revisions;
- Conversation model configuration references;
- Conversation Task/Run/Artifact links;
- Message canonical File/Artifact/Task/Run/Result/Agent/Team references.

Runtime provider availability is not treated as canonical referential integrity. A persisted model configuration may survive while its optional provider/adapter is absent; that remains an intentional portability property.

Knowledge references are not asserted by the single-node validator because the current single-node composition does not own a Knowledge service/store. When such a durable service is composed, it must register its own restore validator through the same extension boundary.

## Remaining #40 acceptance dependency

The single-node backup/restore/recovery contract can be completed independently, but the original #40 acceptance scope also requires proving relocation to different compatible hardware. An earlier #40 clarification explicitly broadened this to different machine-local paths, hostnames, resource layouts and Worker devices.

The current repository still does not have the packaged heterogeneous/multi-device deployment profiles needed to prove that full path. Those profiles are owned by #240.

Therefore:

- this follow-up does **not** claim the hardware/topology E2E criterion is complete;
- #40 should remain open after this PR unless the criterion is formally moved to a dedicated dependent issue;
- once #240 provides the advanced profiles, its relocation acceptance must consume the #40 backup/recovery contract and prove canonical identity/history survive a real topology change;
- #41 remains the owner of cross-version database/platform migrations and must not be folded into #40.

## Required regression evidence for this follow-up

Tests must prove at minimum:

- a missing required durable store prevents backup creation;
- the normal server remains blocked for an orphaned restored Run;
- only a Run named by the blocked restore report can use the offline resolution path;
- operator resolution terminalizes canonically and allows the same readiness gate to become ready;
- a corrupted Conversation/Project relationship is detected by the composed restore validator;
- existing backup/restore, optional-adapter absence, Worker sanitization/re-registration, corruption, compatibility and retry tests continue to pass.
