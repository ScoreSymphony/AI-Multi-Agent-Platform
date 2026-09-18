# Persistence and filesystem failure recovery

Issue: #1155  
Foundation: #707, #13, #16, #37  
Backup/restore authority: #40

## Scope and ownership

The supported single-node profile uses domain-owned SQLite/JSON repositories plus platform-owned
filesystem roots. #1155 hardens failure handling around those existing owners. It does **not** add a
second persistence database, lifecycle authority, schema authority or backup/restore mechanism.

The durable-store inventory in `backup/inventory.py` is reused only to identify which current
single-node stores are required or optional. Repository-specific constructors/migrations remain the
authority for schema compatibility. In particular, the platform does not invent a common
`PRAGMA user_version` for stores that do not already own one.

## Readiness policy

`SingleNodePersistenceHealthProvider` is a required dependency of the existing aggregated health
provider. Authoritative readiness is withheld when required persistence cannot be trusted.

Each probe checks:

- the data, database, File and Workspace roots exist and are directories;
- a short write + flush + `fsync` + atomic rename + delete round-trip succeeds in each required
  filesystem root;
- free space is measurable;
- every required durable store for the **active deployment profile** exists;
- existing SQLite stores open read-only and pass `PRAGMA quick_check(1)`;
- existing JSON stores are readable UTF-8 JSON.

The public `platform-server`/durable single-node profile declares the complete current required
single-node durable-store inventory. The lower-level internal base composition declares only stores
owned by the domains it actually composes; Connector/Context/Learning/Handoff stores do not become
required merely because they exist in the wider backup inventory. This is an explicit composition
contract, not an existence heuristic, so once the public profile is selected a later disappearance
of one of those extension stores is still blocking.

Optional-store corruption degrades health but does not by itself make the required platform
persistence unavailable. Required-store failure for the active profile is blocking.

The reference free-space policy is:

| State | Remaining space | Readiness |
| --- | ---: | --- |
| healthy | at least 256 MiB | allowed |
| low | 64 MiB to below 256 MiB | degraded but still ready |
| critical | below 64 MiB | unavailable / not ready |

These are reference-profile safety reserves, not hardware sizing requirements. A future deployment
configuration may make them operator-configurable without changing the health contract.

Public diagnostics intentionally use logical components such as `data-root` or
`db/kernel.sqlite3`; absolute host paths and raw OS error text are not exposed by the public health
surface.

## SQLite contention and transactions

SQLite contention is bounded. `SqliteKernelRepository` has an explicit
`busy_timeout_seconds` budget (5 seconds by default); the reference Data/Workspace repositories
also use SQLite's bounded connection timeout. A lock/busy result is normalized to
`ErrorCode.TRANSIENT_FAILURE` with `retryable=True`.

The repository itself does not spin or retry an unknown mutation. Retry count/backoff remains the
calling lifecycle policy, as required by the platform contracts. Existing idempotency keys and
expected revisions make a bounded retry safe where the caller explicitly chooses one.

Kernel commits use an explicit transaction and roll back on stale revisions, integrity errors and
other failures. Regression tests reopen the repository after an injected failed mutation and prove
that an uncommitted stream does not later appear committed.

## Crash-interrupted File writes

A local File write has three states:

1. canonical metadata is inserted as `PENDING`;
2. bytes are written to `.<file_id>.pending` and atomically renamed to `<file_id>`;
3. canonical metadata changes to `READY`.

A process crash can therefore leave a `PENDING` record, a pending temp file, or a final file whose
metadata was never promoted to `READY`.

At provider startup, recovery queries **canonical PENDING metadata first**. That row is the ownership
proof. Only the matching pending/final paths are removed, then the row is tombstoned. A random or
unknown temp file is never deleted merely because its name looks temporary.

If an owned path cannot be cleaned, or the tombstone cannot be persisted, provider construction
fails closed. The incomplete object cannot become a canonical readable File by accident.

Artifacts do not introduce a second filesystem byte store in the supported single-node profile.
Artifact materialization is a canonical link from an already durable File record plus the owning
kernel/domain reference. A failed Artifact-link SQLite mutation therefore rolls back independently;
restart regression coverage proves that the Artifact ID does not later appear while the canonical
File bytes remain intact.

## Workspace materialization recovery

Workspace materializations are execution/cache state, not canonical Workspace/Snapshot identity.
The SQLite Workspace provider already clears stale active Task/Run references while loading durable
metadata.

#1155 additionally removes stale `materialization_<canonical-id>` paths during startup. A path is
eligible only when its full name validates as a canonical materialization ID. Invalid/unowned names
are left untouched.

If an owned stale materialization cannot be removed, Workspace provider startup fails closed. The
operator must restore filesystem access and retry startup.

## Operator diagnostics

Run:

```bash
platform doctor
```

The CLI consumes the canonical Control Plane health/readiness endpoints. Persistence failures appear
inside the aggregated provider's `diagnostics` field and are copied into the
`provider_health` doctor check.

Representative codes:

| Code | Meaning | Automatic action | Operator action |
| --- | --- | --- | --- |
| `persistence_path_unavailable` | required root missing/inaccessible | none | restore/mount the root, retry |
| `persistence_path_unwritable` | write/fsync/rename/delete probe failed | probe again on next health request | restore permissions/filesystem |
| `free_space_low` | warning reserve crossed | remain degraded | free space |
| `free_space_critical` | minimum reserve crossed | readiness blocked | free space before writes resume |
| `required_store_missing` | required durable-store path absent | none | recover via owning subsystem / #40 when appropriate |
| `sqlite_temporarily_unavailable` | SQLite lock/busy condition | next probe may recover | clear prolonged lock contention |
| `sqlite_open_failed` | required SQLite store inaccessible | none | restore access |
| `sqlite_integrity_failed` | SQLite quick check failed | **no repair** | stop writes; recover through owner / backup policy |
| `json_store_unavailable` | JSON store inaccessible | next probe may recover | restore access |
| `json_store_invalid` | persisted JSON is malformed | **no repair** | recover through owning subsystem |
| `file_temp_state_unowned` | temp File state has no canonical PENDING owner | none; remain degraded | inspect/remove manually; never promote it |

The probe emits #16 structured transition telemetry for persistence
`unavailable`, `degraded`, and `recovered` states. Telemetry is derived state and never becomes
persistence authority.

## Automatic versus manual recovery

Automatic and deterministic:

- transient health failures clear when the same required path/store becomes available again;
- a crash-interrupted File write proven by `PENDING` metadata is tombstoned and its owned bytes are
  removed;
- stale canonical Workspace materialization directories are removed at provider restart;
- failed SQLite transactions remain rolled back.

Manual/operator action required:

- missing canonical durable stores;
- SQLite integrity failure;
- repository-specific unsupported/incompatible schema;
- malformed durable JSON;
- cleanup that cannot be completed because of permissions or I/O failure;
- unknown temp files without canonical ownership evidence.

Unknown/corrupt canonical state is never silently reconstructed from filenames or best-effort
guesses.

## Failure-injection helpers

`ai_multi_agent_platform.testing` exports:

- `SqliteWriteLock` for deterministic real writer contention;
- `FailOnceFilesystemOperation` for one-shot filesystem failures such as atomic rename.

They are deliberately small and reusable by #46/#1157 conformance/reliability suites. The existing
persistence-fault benchmark remains useful for retry/idempotency pressure; these helpers add the
local OS/SQLite boundary cases that benchmark intentionally did not claim.

## Backup/restore boundary

If canonical state is missing or corrupt, follow `docs/operations/BACKUP_RESTORE.md` and #40.
#1155 does not repair a corrupt database in place and does not reinterpret backup manifests. Health
and startup recovery are pre-serving safety gates around the existing persistence owners.
