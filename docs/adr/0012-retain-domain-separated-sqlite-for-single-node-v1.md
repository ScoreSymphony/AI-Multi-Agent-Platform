# ADR 0012 — Retain domain-separated SQLite persistence for single-node v1

- **Status:** Accepted
- **Issue:** #891
- **Date:** 2026-09-12

## Context

The supported single-node profile currently inventories 25 platform-owned SQLite databases: 21 required stores and 4 optional stores. That layout grew from domain/provider ownership rather than from one explicit platform-wide topology decision. Issue #891 therefore asks whether logical domain separation still needs physical database separation or whether the v1 baseline should consolidate relational state into one `platform.sqlite` database.

The current durable-state boundary is broader than SQLite. The same deployment also has JSON persistence, filesystem-backed File/Workspace content and replaceable or external providers. Whole-platform backup already quiesces the source and validates semantic relationships across those boundaries. Cross-domain operations also use canonical IDs, deterministic idempotency, compensation and reconciliation where one logical action spans independently owned state or external side effects.

The repository has a canonical SQLite contention benchmark that exercises synchronized concurrent Task mutations through multiple `SqliteKernelRepository` instances sharing one database. It proves shared-file writer contention can and must be measured through the canonical path; it does not provide a retained platform-wide comparison of the current 25-file topology against a hypothetical consolidated schema. No performance claim about either topology is therefore treated as established evidence.

The full inventory, cross-store coupling audit and migration considerations are recorded in `docs/runtime/PERSISTENCE_TOPOLOGY.md`.

## Decision

The supported single-node v1 profile **retains domain-separated SQLite files**. We do not migrate current relational state into one `platform.sqlite` as part of #891.

This is now an explicit physical-topology decision, not an accidental implementation detail. It is also not a permanent requirement of platform contracts: SQLite paths remain adapter/deployment details behind domain-owned repositories and provider-neutral interfaces.

### Logical ownership remains independent from physical placement

Each domain continues to own its repository contract, invariants, canonical IDs and schema evolution. Callers must not use database file placement as an ownership shortcut. No component gains authority to query or mutate another domain merely because a future provider might co-locate tables.

Likewise, separate SQLite files are not promoted into a security boundary. Authentication, authorization, Approval, tenant/scope checks, retention and audit rules remain explicit application/repository responsibilities.

### No cross-domain SQL unit-of-work is introduced for v1

The audit found cross-store references and logical workflows, including Automation→Task creation, governance→Task execution, Workspace/File/Artifact relationships, security scopes/identities, verification/evaluation/learning evidence and portability/materialization flows. Those boundaries do not currently establish a requirement for one native SQLite transaction across domain repositories.

Several important workflows also span filesystem, JSON, remote providers or completed external side effects. Consolidating only SQLite metadata would not make those workflows atomic and would not remove the need for deterministic idempotency, compensation, reconciliation or semantic restore validation.

If a future operation has a real correctness invariant requiring atomic writes across repositories, that requirement must be specified directly. A transaction/unit-of-work contract may then be introduced narrowly and implemented by providers capable of honoring it.

### Backup/restore remains a whole-platform recovery boundary

One SQLite file would simplify the relational subset of backup, but the supported backup still needs consistency with filesystem content, JSON stores and provider dependency evidence. Therefore #40's quiesced capture, staged restore and semantic integrity gate remain necessary under either SQLite topology.

The current backup store contract v2 explicitly inventories the existing database paths. Changing them requires a deliberate versioned backup/upgrade migration, not an unversioned path rewrite.

### Concurrency and failure blast radius remain explicit trade-offs

Keeping separate files avoids forcing unrelated domain writers through one SQLite database writer serialization point and keeps database-level corruption/failure physically narrower. Consolidation could reduce file-management overhead and permit selected local SQL transactions, but it would couple repository schema lifecycle and widen the relational failure blast radius.

Because the repository currently lacks a retained representative platform-wide benchmark proving either topology materially faster, performance is not used as a deciding claim. A future performance-driven topology change must include comparative evidence.

### Postgres and other central SQL providers remain possible

The v1 SQLite layout does not become a canonical storage contract. A future Postgres/central SQL implementation may place domain-owned tables in one server/database while still implementing the same repository contracts and canonical ID rules.

Any provider-level cross-repository transaction capability must be surfaced through an explicit platform contract. Raw database handles or ad-hoc cross-domain SQL must not leak into domain services.

## Consequences

- No data migration is required for #891, so existing canonical IDs/history and backup compatibility remain unchanged.
- The 25-file SQLite inventory remains the supported single-node v1 relational topology and is now documented explicitly.
- #40 backup/restore continues to operate over the durable-store contract and semantic cross-store validation.
- #41 remains the required mechanism for any future physical topology migration.
- Existing idempotency/compensation/reconciliation remains necessary where workflows span independent providers or external effects.
- Independent domain writers retain separate SQLite writer-lock domains.
- The relational state retains a narrower per-file corruption/failure blast radius at the cost of more physical artifacts and more cross-store recovery bookkeeping.
- Schema evolution remains repository-owned; there is still no uniform platform contract assigning a SQLite `PRAGMA user_version` to every file.
- A future consolidation proposal has a clear evidence burden rather than starting from an assumption that one file is simpler or faster in every relevant dimension.

## Re-evaluation criteria

Revisit this decision when at least one of the following is demonstrated:

1. a canonical operation requires atomic writes across current SQLite repositories and existing idempotency/compensation semantics cannot satisfy correctness;
2. measured backup/restore, startup, migration or operational cost of the file count is material for the supported single-node envelope;
3. a representative topology benchmark shows meaningful reliability, latency or throughput advantage for consolidation;
4. a central SQL/Postgres provider is introduced and a narrowly scoped cross-repository transaction contract is justified;
5. independent physical schema/version management becomes a recurring release or incident risk.

Any re-evaluation must compare current multi-file SQLite, consolidated SQLite, and the relevant central SQL option. A hybrid topology remains valid if only a small set of repositories has a demonstrated shared-transaction requirement.

## Migration boundary if this decision changes

A future consolidation must be implemented as an explicit #41 upgrade transition coordinated with #40. It must stage the target database, preserve canonical IDs/history/timestamps/idempotency evidence, validate source-to-target counts and semantic references, test concurrent workload and newly claimed atomic operations, produce and restore a compatible backup, and define rollback/downgrade behavior before publish.

The durable-store contract version must change deliberately when physical paths/required stores change. Destructive in-place copying without verified pre-upgrade rollback evidence is not acceptable.

## Alternatives considered

### Consolidate all SQLite stores into one `platform.sqlite` now

Rejected for v1. It would provide one native transaction/snapshot boundary only for state actually moved into that file. Filesystem content, JSON stores, replaceable providers and external side effects would remain outside it. The migration would also couple independent repository schemas, widen the relational writer/failure blast radius and change the backup store contract without a demonstrated correctness or measured performance requirement.

### Hybrid consolidation now

Rejected for #891 because the audit did not identify a specific subset whose shared native transaction is currently required for correctness. Hybrid remains the preferred shape to evaluate first if such a requirement appears later, because it can avoid moving unrelated domains merely to reduce file count.

### Make one central SQL/Postgres database mandatory for v1

Rejected because local/single-node deployment is a first-class profile and platform storage is intentionally provider-neutral. A central SQL implementation may be added later without making it mandatory for local users.

### Treat current separation as permanent domain architecture

Rejected. Physical file placement is an adapter/deployment choice. The durable interfaces, canonical IDs and domain ownership rules must survive a future shared-database or Postgres implementation.

## Affected issues and contracts

- #891 owns this topology decision and the persistence inventory/audit.
- #13 owns provider-neutral data persistence contracts and canonical-ID invariants.
- #39 owns the supported single-node deployment/profile composition boundary.
- #40 owns backup/restore and the authoritative durable-store inventory contract.
- #41 owns future upgrade/migration transitions if physical topology changes.
- #892 may change SQLite execution/offload behavior independently of this topology decision and should not silently alter physical ownership.
- ADR 0009 keeps the initial HA model single-writer active/passive and requires stronger evidence before concurrent lifecycle writers are supported.
- `docs/runtime/PERSISTENCE_TOPOLOGY.md` records the detailed audit and re-evaluation/migration requirements.
