# ADR 0002: Separate canonical persistence, files, scoped memory and knowledge

- Status: Accepted
- Date: 2026-09-02
- Issue: #13

## Context

Canonical platform state, durable files, memory and retrieval indexes all persist data, but they have different truth, lifecycle and authorization semantics. Treating them as one storage abstraction would let a database, vector engine or object store become an accidental architecture dependency. Memory also needs explicit scopes so it cannot become a generic bucket for canonical lifecycle history.

## Decision

1. The Task/Run/Event kernel repositories remain authoritative for canonical lifecycle state and event history.
2. Files/artifacts, scoped memory and knowledge use independent platform-owned provider contracts.
3. Issue #13 refines the coarse issue-#5 provider interfaces in `ai_multi_agent_platform.data`; the refined interfaces subclass the existing core interfaces.
4. Canonical data identities use platform-owned UUID-prefixed IDs. Raw paths, backend primary keys, object keys and vector IDs are never canonical.
5. Memory has six explicit scopes: short-term, task, agent, workspace/project, user and historical.
6. Historical memory is derived mutable data and requires provenance back to source evidence. It cannot replace immutable canonical history.
7. No canonical memory operation requires embeddings or a vector database.
8. Knowledge search capability is provider-declared. Keyword, semantic and hybrid implementations are interchangeable; the baseline implements keyword retrieval only.
9. The baseline self-hosted implementation uses SQLite for metadata/memory/knowledge and the local filesystem for file bytes.
10. Cross-provider operations do not claim distributed atomicity. Providers expose integrity/tombstone/reconciliation hooks instead.

## Consequences

### Positive

- storage technologies remain replaceable;
- the platform can run locally with no cloud/vector dependency;
- project/user scope boundaries are explicit before final authorization is implemented;
- canonical reconstruction remains independent of mutable memory/search indexes;
- a future Control Plane can inject providers through `DataProviderSet`.

### Costs

- adapters must translate backend IDs and errors;
- file metadata and bytes require explicit two-step consistency handling;
- richer authorization and retention policy remain follow-up work rather than hidden backend behavior.

## Follow-ups

- #15 applies final authorization/approval policy to the preserved access context.
- #33 assigns agent/team memory and knowledge access.
- #37 builds portable workspaces on the file/artifact boundary.
- #45 may add global discovery indexes, which remain derived and non-authoritative.
