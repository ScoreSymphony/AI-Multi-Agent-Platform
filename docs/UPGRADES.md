# Platform upgrades and database/schema migrations

Issue #41 owns **cross-release deployment migration**. It coordinates persistent canonical data,
API/domain contracts, plugins/adapters, portable packages and recovery requirements when an
installed platform moves between supported releases.

It does **not** redefine the portable package format from #79 and it does not replace the
backup/restore semantics from #40.

## Version dimensions are independent

A deployment records the following dimensions separately:

| Dimension | Current source | Purpose |
| --- | --- | --- |
| Platform release | `ai_multi_agent_platform.__version__` | Product/release identity |
| Canonical domain schema | `domain.SCHEMA_VERSION` | Persisted canonical object interpretation |
| Control Plane API | `control_plane.API_VERSION` | Client/API compatibility namespace |
| Migration revision | upgrade history/version state | Exact applied cross-release migration state |
| Plugin manifest | `plugins.PLUGIN_MANIFEST_VERSION` | Plugin metadata contract |
| Plugin interface versions | installed extension metadata | Extension contract compatibility |
| Adapter versions | deployment/adapter metadata | Concrete adapter/upstream compatibility |
| Portable package format | `portability.PORTABLE_FORMAT_VERSION` | #79 canonical import/export envelope |
| Template portable schema | template portability codec | Stored/portable Template interpretation |
| Backup format | `backup.BACKUP_FORMAT_VERSION` | #40 recovery artifact interpretation |
| Worker protocol | `distributed.WORKER_PROTOCOL_VERSION` | Worker registration/dispatch compatibility |
| Message protocol | `messaging.ENVELOPE_VERSION` | Distributed message-envelope compatibility |

None of these values implies another. For example, API `v1` does not imply domain schema `1.0`,
and a platform patch release does not imply a new migration revision.

The durable deployment marker is `db/platform-upgrade.json`. Existing 0.0.1 installations adopt
that marker once with:

```bash
platform-upgrade --data-dir /srv/ai-map/data initialize
```

Baseline adoption records the current release; it does not rewrite canonical application state.
Future releases must never silently invent an earlier migration history for an untracked data root.

## Migration framework

Production migrations are immutable `MigrationStep` records registered in release order. Every
step declares:

- a stable sequence and revision ID;
- exact source and target canonical schema versions;
- description and deterministic metadata checksum;
- whether the step can run transactionally;
- whether retry after a recorded failure is safe;
- whether a verified source-release backup is mandatory;
- rollback classification;
- mutation and optional post-migration validation callbacks.

`db/migration-history.json` records `started`, `applied` and `failed` state. Re-running an already
applied migration is a no-op only when the immutable metadata checksum still matches. A changed
published migration is rejected.

A failed migration is never silently retried. Explicit resume is permitted only when the migration
was declared restart-safe. Otherwise recovery means restoring the verified pre-upgrade backup and
starting again from the source release.

### Database neutrality

The registry, planning logic and migration history are not tied to SQLite. A concrete persistence
implementation supplies a transaction context to `MigrationContext` when it can provide one.
Non-transactional phases must be declared explicitly and must have an appropriate backup/recovery
classification.

## Canonical contract evolution

Supported evolution follows these rules:

- additive optional fields are preferred within an existing contract version;
- making an optional field mandatory requires an explicit migration/backfill or a new incompatible
  contract version;
- enum/status additions must preserve the behavior of historical persisted values;
- renamed/removed fields require explicit translators/backfills; fields are never silently reused
  with a different meaning;
- canonical identifier changes are strongly discouraged and require deterministic reference
  migration across every owning/dependent store;
- adapter/provider-native metadata remains namespaced and may be migrated only as namespaced
  metadata, never promoted into canonical identity;
- historical Event payloads must remain interpretable for every schema version explicitly supported
  by the release. Event `1.0` and `2.0` are the currently published historical contracts;
- rebuilt indexes/caches are not evidence that canonical history migrated successfully.

## API compatibility

The existing Control Plane rules remain authoritative: additive compatible evolution can occur
inside the current API major namespace, while incompatible semantics require a new API namespace.
#41 does not declare mixed-version API operation merely because two server versions can start.

Upgrade preflight fails closed on an API major/version transition unless that release adds an
explicit compatibility policy.

## Plugins and adapters

Before a new release is activated, the deployment composition supplies installed Plugin manifests
and adapter compatibility metadata to preflight.

For plugins, preflight checks:

- declared supported platform range;
- extension interface versions;
- required versus optional status.

For adapters, preflight checks their declared platform and interface requirements. An incompatible
**required** extension blocks activation. An incompatible **optional** extension is reported as a
warning and must remain disabled until compatible. Plugin-owned state migrations are invoked only
through controlled plugin lifecycle hooks; they do not bypass the platform migration record or
security boundaries.

## Portable import/export compatibility (#79)

#79 owns the current portable package contract. #41 owns whether a package produced by an older
release remains acceptable after a platform upgrade.

Rules:

1. the current portable format is accepted directly;
2. an older format is accepted only when an explicit translator chain exists;
3. translation is deterministic and version-to-version; there is no best-effort reinterpretation;
4. an unsupported format is rejected before import mutation;
5. translators preserve #79 canonical ID/reference semantics and do not introduce backend-private
   runtime state;
6. previously exported packages are not rewritten in place simply because the platform upgrades.

`FormatTranslatorRegistry` implements this cross-release policy without redefining #79 serializers.

## Template compatibility (#78/#79)

Template packages follow the same explicit translator rule. Existing resources instantiated from an
older Template remain independent of later Template revisions. Stored Template definitions are
migrated only through explicit schema rules; a later Template version never silently mutates an
already-instantiated Agent, Team, Automation or other canonical resource.

## Preflight is mutation-free

Run:

```bash
platform-upgrade --data-dir /srv/ai-map/data preflight
```

Optional inputs let operators validate a concrete backup and historical package versions:

```bash
platform-upgrade --data-dir /srv/ai-map/data preflight \
  --backup-dir /srv/backups/pre-upgrade \
  --portable-version 1.0 \
  --template-version 1 \
  --historical-event-version 1.0 \
  --historical-event-version 2.0
```

Preflight evaluates before mutation:

- installed and target version vectors;
- existence of one unambiguous migration path;
- filesystem readability/writability and free space threshold;
- SQLite `PRAGMA quick_check` for SQLite files in the single-node data root;
- unresolved migration failures;
- required/optional plugin and adapter compatibility supplied by the deployment composition;
- declared configuration-schema transitions;
- historical Event schema interpretability;
- portable/template format translator availability;
- API, Worker and message protocol changes;
- backup presence and #40 integrity verification when a migration marks backup as mandatory.

Warnings do not become hidden assumptions: they remain in the report. Errors make `report.ok=false`
and prevent `UpgradeService` from mutating state.

## Maintenance and drain

A schema-changing upgrade requires an explicit quiesced/drained assertion. The deployment layer is
responsible for implementing the actual policy appropriate to that topology:

- stop accepting/dispatching new work;
- pause Automation-triggered task creation where applicable;
- drain active Worker jobs or cancel them according to operator policy;
- ensure no old process continues to mutate stores during migration;
- stop/restart mixed-version Workers when the Worker protocol policy requires it.

The upgrade service writes `db/upgrade-maintenance.json` before the first migration. It clears that
marker only after all migrations validate, the target version vector is durably recorded and the
upgrade result is persisted. A failed/interrupted migration therefore leaves a visible maintenance
marker and does not advertise the target schema as active.

Single-node planned downtime is a supported baseline. Expand/migrate/contract or rolling migration
patterns can be added later only when the relevant persistence/protocol semantics prove them safe.

## Rollback classes

Every migration is classified explicitly:

### `reversible`

The migration has a proven reverse path. Code rollback is still valid only when the old code can
interpret the restored/reversed schema.

### `code_only_before_migration`

Rolling back the executable is safe **before** data migration. Once the migration mutates state,
operators must follow the migration-specific recovery instructions rather than assuming old code can
read the new schema.

### `restore_required`

The transformation is forward-only. Preflight requires a verified #40 backup produced by the source
release. Recovery is restore-from-backup; the platform never labels this path reversible.

A backup created under the source release is retained as the recovery artifact even when the target
release introduces a newer backup format.

## Supported operator workflow

For a real release upgrade:

1. stop and inspect the current deployment; run `platform-upgrade ... versions`;
2. run `platform-upgrade ... preflight` with the intended source backup/package evidence;
3. create a quiesced backup with `platform-backup create` and verify it with #40 tooling;
4. pause new Task/Automation dispatch and drain/cancel active work according to policy;
5. stop old processes and ensure the deployment is quiesced;
6. install the target release without deleting the durable data root;
7. run target-release preflight again against the same data root and verified backup;
8. run `platform-upgrade ... apply --quiesced` (and `--backup-dir` when required);
9. start the new Control Plane/Workers and validate readiness, plugin/adapter/protocol compatibility;
10. run canonical Task/Run smoke and inspect `platform-upgrade ... maintenance`;
11. resume normal work only after validation succeeds and retain the recorded upgrade result.

If apply fails, do not simply restart normal services. Inspect migration history and the maintenance
marker. Explicit `--resume-failed` is valid only for a migration declared restart-safe; otherwise
restore the pre-upgrade backup on the source-compatible release.

## Release author checklist

A release that changes persistent/domain state must:

1. add immutable migration steps to `default_migration_registry()`;
2. provide an older-release fixture representing the exact supported source state;
3. test preflight, apply, already-applied behavior and failure recovery;
4. declare transaction/restart/backup/rollback semantics for every step;
5. update portable/template translators only for versions the release intentionally supports;
6. update plugin/adapter/API/Worker/message compatibility evidence where those contracts change;
7. preserve historical Event interpretation or ship an explicit translator/backfill;
8. document unsupported direct upgrade paths rather than guessing;
9. run #40 backup/restore and the release acceptance suite before claiming upgrade compatibility.

The first implementation release (`0.0.1`) contains the migration framework and baseline adoption
but no fabricated production migration from a schema that was never released. Tests use a controlled
`0.9 -> 1.0` fixture to prove the cross-release mechanism. The first real schema-changing release
must replace that proof fixture with an actual previous-release fixture and immutable production
migration entry.
