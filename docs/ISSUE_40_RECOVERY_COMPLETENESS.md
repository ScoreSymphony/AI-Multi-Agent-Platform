# Issue #40 recovery-completeness follow-up

Issue #40 was reopened after the merged #278 hardening was audited again against the original issue text rather than the PR summary.

## Why the issue was reopened

Three current single-node gaps remained:

1. an orphaned restored `RUNNING` Run correctly blocked normal serving but had no offline operator path that could resolve it while the Control Plane was unavailable;
2. backup verification required the durable component directories and canonical kernel database but did not require the other eagerly-created durable stores of the current single-node composition;
3. the post-restore integrity gate validated core Task/Run/Project/Workspace/File relationships but had no extension boundary for additional durable subsystems such as Agents and Conversations.

## Changes in this follow-up

### Offline restored-Run resolution

`platform-server resolve-restore-run` is intentionally available while normal serving is blocked. It accepts only a Run that:

- appears in the authoritative blocked `restore-report.json`;
- belongs to the supplied Task in that report;
- remains canonically `RUNNING`;
- is explicitly marked `recovery_required`.

The operator may record `cancelled` or `failed` with a required reason. The command uses the existing canonical `PlatformKernel.record_run_outcome()` path rather than inventing a backup-specific lifecycle state, then immediately reruns the complete restore recovery/readiness gate. It does not allow a successful outcome because a replacement host cannot prove that a lost execution completed successfully.

### Durable-store inventory

`backup/inventory.py` is the single source of truth for durable stores currently composed by the single-node profile. Eager SQLite stores are required in every healthy backup:

- `db/kernel.sqlite3`
- `db/scopes.sqlite3`
- `db/files.sqlite3`
- `db/workspaces.sqlite3`
- `db/verification.sqlite3`
- `db/authentication.sqlite3`
- `db/authorization.sqlite3`
- `db/automation.sqlite3`

Lazy JSON stores are inventoried but are optional until their owning subsystem persists state:

- `db/agents.json`
- `db/conversations.json`
- `db/models.json`
- `db/model-providers.json`
- `db/onboarding-commands.json`

Runtime manifest validation rejects an archive that omits any required eager store, so an incomplete assembly cannot be published as a healthy backup.

### Extensible restore integrity

`RestoreIntegrityValidator` lets durable subsystems participate in the pre-serving gate without moving their domain logic into the backup module. The single-node composition currently registers validators for:

- Agent / Agent revision / Team / Team revision / AgentRun references;
- Conversation / Message references, including Project, Workspace, Task, Run, Artifact, Agent, Team and model-configuration references.

Unexpected validator failures are converted to `RestoreValidationError`, and normal service remains blocked.

## Remaining cross-issue acceptance

This follow-up intentionally does **not** claim the heterogeneous replacement-hardware acceptance criterion is complete. Issue #40 owns the durable relocation/recovery contract, while #240 owns the advanced distributed/heterogeneous deployment profiles needed to prove a full end-to-end relocation across different hostnames, resource layouts and Worker devices with authenticated Worker re-registration.

Accordingly, this follow-up references #40 but should not close it automatically. The final #40 closure should happen only after the #40 contract has been consumed by the relevant #240 relocation test, or after the issue text is explicitly changed to move that acceptance criterion entirely to #240.
