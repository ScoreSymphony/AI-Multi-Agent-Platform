# Single-node persistence topology

Issue #891 re-evaluates the physical SQLite topology of the supported single-node profile. This document records the current durable-store inventory, the operations that cross those physical boundaries, the operational trade-offs, and the evidence used by ADR 0012.

The important distinction is:

- **logical ownership** is defined by platform contracts, domain services and repositories;
- **physical topology** is an adapter/deployment choice;
- one physical database would not make unrelated domains one logical aggregate;
- multiple physical databases are not, by themselves, a security boundary.

The authoritative machine-readable durable-store inventory remains `src/ai_multi_agent_platform/backup/inventory.py`. This document explains that inventory rather than replacing it.

## Current SQLite inventory

The single-node durable-store contract is currently version 2. It declares 25 SQLite stores: 21 required stores and 4 optional stores. The backup contract also includes JSON stores and the deployment owns filesystem-backed content/workspaces, so SQLite is only one part of the durable-state boundary.

There is no platform-wide contract that assigns an independent `PRAGMA user_version` value to every SQLite file. Physical schema evolution is repository-owned and upgrade history is tracked by the platform upgrade subsystem. Consequently the table below records the **schema-version authority** accurately as repository-local rather than inventing per-file version numbers that do not exist as a canonical contract. If a future topology migration is implemented, explicit source/target schema versions must be part of that migration.

| Store ID | Path | Required | Owner | Physical schema/version authority |
| --- | --- | ---: | --- | --- |
| `kernel` | `db/kernel.sqlite3` | yes | kernel | repository-local; backup store contract v2 inventories the file |
| `coordination` | `db/coordination.sqlite3` | yes | coordination | repository-local; backup store contract v2 inventories the file |
| `scopes` | `db/scopes.sqlite3` | yes | control-plane | repository-local; backup store contract v2 inventories the file |
| `files` | `db/files.sqlite3` | yes | data | repository-local; backup store contract v2 inventories the file |
| `workspaces` | `db/workspaces.sqlite3` | yes | workspaces | repository-local; backup store contract v2 inventories the file |
| `run-workspace-bindings` | `db/run-workspace-bindings.sqlite3` | yes | workspaces | repository-local; backup store contract v2 inventories the file |
| `repository-bindings` | `db/repository-bindings.sqlite3` | yes | repositories | repository-local; backup store contract v2 inventories the file |
| `repository-provenance` | `db/repository-provenance.sqlite3` | yes | repositories | repository-local; backup store contract v2 inventories the file |
| `connectors` | `db/connectors.sqlite3` | yes | connectors | repository-local; backup store contract v2 inventories the file |
| `memory` | `db/memory.sqlite3` | yes | context | repository-local; backup store contract v2 inventories the file |
| `knowledge` | `db/knowledge.sqlite3` | yes | context | repository-local; backup store contract v2 inventories the file |
| `research` | `db/research.sqlite3` | yes | research | repository-local; backup store contract v2 inventories the file |
| `handoffs` | `db/handoffs.sqlite3` | yes | handoffs | repository-local; backup store contract v2 inventories the file |
| `verification` | `db/verification.sqlite3` | yes | verification | repository-local; backup store contract v2 inventories the file |
| `evaluation` | `db/evaluation.sqlite3` | yes | evaluation | repository-local; backup store contract v2 inventories the file |
| `learning` | `db/learning.sqlite3` | yes | learning | repository-local; required since backup store contract v2 |
| `learning-post-promotion` | `db/learning-post-promotion.sqlite3` | yes | learning | repository-local; required since backup store contract v2 |
| `authentication` | `db/authentication.sqlite3` | yes | security | repository-local; backup store contract v2 inventories the file |
| `authorization` | `db/authorization.sqlite3` | yes | security | repository-local; backup store contract v2 inventories the file |
| `approvals` | `db/approvals.sqlite3` | no | security | repository-local; lazy/optional store in backup contract v2 |
| `governance` | `db/governance.sqlite3` | no | governance | repository-local; lazy/optional store in backup contract v2 |
| `decisions` | `db/decisions.sqlite3` | no | decisions | repository-local; lazy/optional store in backup contract v2 |
| `authorization-audit` | `db/authorization-audit.sqlite3` | no | security | repository-local; lazy/optional store in backup contract v2 |
| `automation` | `db/automation.sqlite3` | yes | automation | repository-local; backup store contract v2 inventories the file |
| `notifications` | `db/notifications.sqlite3` | yes | notifications | repository-local; backup store contract v2 inventories the file |

The same backup contract currently inventories JSON persistence for Agents, Conversations, Models, model providers, onboarding commands, Templates, Workflows, capability assignments, distributed runtime state, and upgrade/migration history. A future SQL consolidation therefore would not collapse the full durable platform into one transaction boundary unless those independent stores and filesystem-backed content were also redesigned.

## Logical boundaries that cross physical stores

The current platform intentionally permits canonical references across domain-owned repositories. Cross-store references are not evidence that the domains should be merged, but they matter for restore validation and for operations that mutate more than one authority.

### Automation to canonical Task history

Automation persists its own definitions, trigger/delivery state and provenance while generated Tasks are created through the ordinary Control Plane/kernel path. Deterministic idempotency keys make retry safe across that boundary. A shared SQLite file could technically make some local mutations eligible for one SQL transaction, but only by introducing a cross-domain unit-of-work that bypasses the current repository-level composition. It would not make connector/webhook/external trigger effects transactional.

### Governance to Task execution

Governance can reserve and bind Task identity while canonical Task creation and provenance live under kernel/control-plane authorities. The current design uses stable IDs and deterministic recovery rather than assuming one storage transaction.

### Workspace, File and Artifact relationships

Workspace metadata and run bindings reference Projects, Runs, Files, Snapshots and Artifacts owned elsewhere. Restore integrity verifies these relationships explicitly. Even if all relational metadata moved into one SQLite file, actual workspace/File content remains filesystem/provider-owned and therefore outside the SQL transaction.

### Authentication and authorization references

Authentication credentials and authorization scopes reference canonical users, Automations, Agents, Projects and Workspaces. The security repositories remain their own authorities. Physical co-location would not permit callers to bypass repository and policy boundaries.

### Verification, evaluation, research and learning

Verification and evaluation evidence references canonical Task/Run/Project/Artifact/Agent/model identities. Learning adds promotion/post-promotion state that may depend on evaluation and knowledge evidence. These are intentionally linked by canonical IDs, not foreign ownership of the referenced entities.

### Portability and multi-provider mutation

Portable import/materialization flows can create resources through multiple canonical providers and use dependency-ordered reverse compensation when a later mutation fails. A single SQLite file cannot remove this class of compensation because providers may be filesystem-backed, JSON-backed, remote, replaceable, or externally side-effecting.

### Compensation of external side effects

The compensation subsystem exists for completed provider/external side effects after canonical failure/cancellation. Those effects are intentionally not transactional rollback. Physical SQLite consolidation does not change the semantics or remove the need for reconciliation after an ambiguous external result.

## Restore and backup consequences

The current backup implementation treats the durable-store inventory as one recovery artifact. For an ordinary SQLite/filesystem backup the source is quiesced before capture; verification then checks checksums, store-contract compatibility, SQLite integrity and semantic cross-store references before a staged restore is published.

A single `platform.sqlite` would reduce the number of SQLite files copied and verified, but it would **not** eliminate the quiescence requirement for a whole-platform consistent backup because:

- filesystem workspace/File content remains outside SQLite;
- optional JSON stores remain outside SQLite;
- external/provider dependencies are represented as recovery evidence rather than copied into SQLite;
- restore still needs semantic reference validation, not only SQL integrity.

The current multi-file topology therefore has a real operational cost, but one-file consolidation would remove only part of that cost.

## Concurrency evidence

The repository already contains `PersistenceContentionBenchmarkHarness`, which drives synchronized concurrent canonical Task mutations through independent `SqliteKernelRepository` instances sharing one SQLite database. It records mutation latency, throughput, peak in-flight work, SQLite busy/locked failures, canonical conflicts, resource use and reopen correctness.

That harness establishes an important boundary for #891: shared-file SQLite writer contention is measurable through the canonical path and must not be dismissed by assumption. It does **not** compare the complete 25-file topology with a hypothetical consolidated schema, and the repository currently contains no retained representative benchmark proving that either physical topology is faster for the platform-wide workload.

Accordingly ADR 0012 does not use an unmeasured performance claim to justify either consolidation or retention. The v1 decision is based on transaction semantics, failure/blast-radius properties, backup scope, migration cost and provider neutrality. A future proposal to consolidate on performance grounds must add a topology-comparative workload rather than extrapolating from the kernel-only contention benchmark.

## Failure and debugging trade-offs

### Multi-file advantages

- independent domains do not share one SQLite writer lock;
- corruption or unrecoverable database-level failure can be physically narrower;
- optional stores can appear/disappear without rewriting a monolithic schema;
- domain repositories can evolve implementation details independently;
- the topology mirrors replaceable provider boundaries and does not imply cross-domain SQL ownership.

### Multi-file costs

- no ordinary native transaction spans repositories/files;
- backup/restore inventories and verifies more physical objects;
- incident reconstruction must correlate canonical IDs across stores;
- some failure paths need idempotency, compensation or reconciliation;
- schema/version bookkeeping is less visible because there is no uniform physical SQLite schema-version contract.

### Single-file advantages

- one SQLite-level snapshot/transaction boundary for all tables actually placed in that file;
- fewer SQLite artifacts to copy, integrity-check and inspect;
- local cross-domain unit-of-work could be implemented for a deliberately selected set of operations;
- SQL-level incident inspection could join local metadata more easily.

### Single-file costs

- all co-located domains contend on SQLite's database writer serialization;
- database corruption/failure has a wider relational-state blast radius;
- independent repository migrations become coupled to a shared physical schema lifecycle;
- a shared file can tempt accidental cross-domain SQL and erosion of provider-neutral repository contracts;
- migration must transform backup-store contract v2 and preserve every canonical ID/history while coordinating rollback;
- it still cannot atomically include filesystem, JSON, remote providers or external side effects.

## Security and retention

The current file split must not be treated as strong security isolation. In the ordinary single-node process the platform can open the stores it composes, so authorization and tenant/scoping rules remain application/repository responsibilities.

Some stores nevertheless have distinct lifecycle or audit properties, especially authentication, authorization, approvals and authorization audit evidence. Those requirements should remain explicit if a future implementation co-locates tables. A shared database must not become permission to expose cross-domain SQL or to relax retention/audit controls.

## V1 topology decision

ADR 0012 keeps the current domain-separated SQLite topology for the supported single-node v1 profile. This is a deliberate decision rather than an accidental accumulation of files.

The decision does **not** declare the current 25-file layout permanently optimal. It declares that physical consolidation is not justified yet because:

1. there is no demonstrated platform operation whose correctness currently requires a cross-domain native SQLite transaction;
2. a single SQLite file would not include filesystem, JSON, remote-provider or external-side-effect state, so it would not remove the platform's need for idempotency, compensation, reconciliation or quiesced whole-platform backups;
3. consolidation would widen the relational writer/failure blast radius and couple independent repository schema evolution;
4. backup store contract v2 already makes the 25 SQLite paths part of supported recovery compatibility, so changing them is a real upgrade/migration event rather than a path cleanup;
5. repository/provider neutrality already preserves a future central SQL/Postgres implementation without first forcing local SQLite domains into one file;
6. no retained platform-wide comparative benchmark proves a performance advantage that would outweigh those costs.

## Re-evaluation triggers

Re-open the topology decision when one or more of these conditions become true:

- a canonical operation needs atomic writes across two or more current SQLite repositories and idempotency/compensation cannot meet its correctness requirements;
- backup/restore or upgrade measurements show the physical file count is a material operational bottleneck;
- a representative topology benchmark demonstrates a meaningful reliability/latency/throughput advantage for consolidation under the supported workload;
- a central SQL/Postgres provider is introduced and can supply a well-defined transaction/unit-of-work contract without collapsing domain ownership;
- per-store migration/version management becomes a recurring source of incidents or materially increases release risk.

A re-evaluation must compare at least current multi-file, consolidated SQLite, and central SQL/Postgres-backed options. A hybrid may be selected when a small set of tightly coupled repositories demonstrably benefits from one transaction boundary while independent/high-risk stores remain separate.

## Requirements for any future consolidation migration

If a later ADR changes the topology, implementation must be an explicit #41 upgrade transition and must coordinate with #40 backup/restore. At minimum it must:

1. introduce an explicit target schema version and table ownership map;
2. detect the exact source durable-store contract/schema state before writes;
3. create the target database in staging rather than destructively rewriting source stores;
4. copy all records while preserving canonical IDs, event/history ordering, timestamps and idempotency records;
5. validate row counts plus domain invariants and cross-store references against source state;
6. prove representative atomic operations and concurrent workload behavior;
7. create a compatible backup before publish and verify restore from that backup;
8. publish the target atomically at the upgrade boundary;
9. retain source-state rollback evidence until the release's rollback window closes;
10. update the durable-store contract version and backup verifier deliberately.

Downgrade support must be declared rather than assumed. If reverse migration cannot be made lossless, rollback must restore the verified pre-upgrade backup instead of attempting an unsafe down-migration.

## Postgres / central SQL seam

The physical v1 choice must not become a provider lock-in. A future central SQL provider should continue to implement platform-owned repository contracts and canonical ID semantics. If it offers a multi-repository transaction/unit-of-work feature, that feature must be an explicit platform contract used only by operations that own a real atomicity invariant.

Callers must not receive raw database handles and must not issue ad-hoc cross-domain joins/mutations. Domain services and repositories remain the authority boundary whether tables are in 25 SQLite files, one SQLite file, or a central Postgres deployment.

## Evidence and related contracts

- `src/ai_multi_agent_platform/backup/inventory.py` — authoritative single-node durable-store inventory and contract version.
- `docs/operations/BACKUP_RESTORE.md` — quiesced capture, staged restore and semantic integrity requirements.
- `docs/operations/UPGRADES.md` — upgrade/migration lifecycle and rollback boundary.
- `docs/runtime/DATA_PERSISTENCE.md` — provider-neutral File/Memory/Knowledge contracts and canonical-ID invariants.
- `docs/runtime/COMPENSATION.md` — compensation/reconciliation semantics for completed external side effects.
- `docs/history/issues/ISSUE_40_CROSS_STORE_AUDIT.md` — cross-store semantic restore audit.
- `src/ai_multi_agent_platform/benchmarking/persistence_contention.py` — canonical shared-SQLite writer contention benchmark harness.
- ADR 0009 — initial HA is single-writer active/passive; active/active lifecycle writers remain unsupported until concurrency semantics prove correctness.
