# Issue #13 Completion Invariants

This note records the hardening required to satisfy the full acceptance criteria and required-test list of issue #13.

## Durable memory provenance

Every non-short-term `MemoryEntry` has provenance. Callers should provide the strongest available canonical evidence (`task`, `run`, `event`, `tool`, `model`, `file`, or another source reference). If a durable entry is written without stronger evidence, the canonical model records a `memory_writer` source pointing to `created_by` so durable memory never exists without a source link.

Historical memory remains stricter: it must be created with explicit provenance and cannot rely on the fallback writer source.

## Access semantics by memory scope

`MemoryAccessPolicy` defines provider-neutral policy subjects and rules for every scope. These are canonical semantics, not backend ACL identifiers. Issue #15 resolves the subjects against authenticated actors and issue #33 resolves agent revision/team membership.

| Scope | Reader/writer baseline | Agent revision rule | Team rule | Task inheritance | Cross-project |
| --- | --- | --- | --- | --- | --- |
| Short-Term | owner + active context | context-bound | deny by default | none | deny |
| Task | owner + authorized task participant | authorized task agent revisions | policy controlled | same task only | deny |
| Agent | owner + authorized agent revision | same-agent, policy controlled | explicit policy only | explicit only | deny by default |
| Workspace | owner + authorized workspace member | workspace policy controlled | workspace policy controlled | explicit only | deny |
| User | owner | explicit user grant only | deny by default | explicit user grant only | explicit policy only |
| Historical | owner + authorized history role | history policy controlled | history policy controlled | none | explicit policy only |

The data provider preserves project/user scope isolation immediately; later authorization layers decide whether a concrete actor matches one of these symbolic subjects.

## Short-term execution association

Short-term memory uses its mandatory `scope_id` as the persisted session/execution reference. Provider calls additionally carry optional canonical Task, Run and Agent IDs in `DataAccessContext`, so authorization/audit integrations can associate the active context without turning backend session identifiers into canonical identity.

## Backend replacement and canonical IDs

Provider replacement must preserve platform-owned IDs. Contract tests move a canonical `MemoryEntry` from the SQLite reference provider to an independent in-memory provider and assert that the same `memory_...` identity survives. Knowledge lifecycle tests likewise assert stable `knowledge_source_...` identity across register, ingest and reindex operations.

## Error mapping

Reference adapters translate backend failures into canonical `ContractError` categories. Tests explicitly cover simulated SQLite failure -> `BACKEND_ERROR`, missing file/knowledge references -> `NOT_FOUND`, project/user boundary violations -> `FORBIDDEN`, and file checksum corruption -> `CONTRACT_VIOLATION`.

## Completion rule

Issue #13 is complete only when format, lint, strict type checking, all tests and package build pass with these hardening tests included. A closed issue or green subset of CI is not sufficient by itself.
