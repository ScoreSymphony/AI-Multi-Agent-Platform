# Search index checkpoints and stale recovery

This document extends the issue #45 Search contract with synchronization metadata for larger or event-driven deployments.

## Invariants

Search index checkpoints are **derived operational metadata**. They are not canonical platform state and never replace canonical resource versions, lifecycle state, authorization or provenance.

The existing correctness-first behavior remains the default: the Control Plane rebuilds Search from canonical sources before every query unless a deployment explicitly opts into checkpointed synchronization.

## Checkpoint contract

`SearchIndexCheckpoint` reports:

- monotonically advancing provider-local `generation`;
- `schema_version` for the derived Search index representation;
- current `document_count`;
- timestamp of the last full successful rebuild;
- `stale` state;
- an optional human-readable stale reason.

The current canonical index schema version is exposed as `SEARCH_INDEX_SCHEMA_VERSION`.

Checkpoint generation is provider-local operational metadata. It must not be used as a canonical resource revision.

## Optional provider capability

`SearchProvider.index_checkpoint(...)` and `SearchProvider.mark_stale(...)` are optional methods with backward-compatible defaults.

A provider that does not implement checkpoint reporting returns `None`. The Control Plane must then preserve correctness by rebuilding before Search rather than assuming freshness.

The local baseline provider implements both operations and advertises `checkpoint` / `mark_stale` in its provider descriptor.

## Local provider semantics

`LocalSearchProvider` behaves deterministically:

- a full `rebuild(...)` atomically replaces the derived documents, increments generation and records a fresh checkpoint;
- `upsert(...)` / `delete(...)` increment generation and document count metadata;
- incremental changes performed before any full rebuild start in a stale state because completeness cannot be proven;
- once a checkpoint has been marked stale, later individual upserts/deletes do **not** clear that condition;
- only a successful full rebuild clears stale state;
- `mark_stale(...)` does not pretend to mutate canonical resources.

This prevents one later incremental event from accidentally hiding the fact that an earlier event was missed.

## Control Plane refresh modes

### Correctness-first default

Rebuild-before-query is the default and preserves the original #45 baseline. Every query rebuilds from canonical sources before provider candidate discovery.

This remains appropriate for the in-memory baseline and for deployments that do not have a durable event-driven indexing pipeline.

### Checkpointed mode

A deployment whose SearchProvider is maintained by canonical write-through/event delivery may explicitly switch the composed Control Plane after construction:

```python
control_plane.configure_search_refresh(rebuild_before_query=False)
```

Configuration is intentionally not a Control Plane constructor argument. The platform composes many cooperative Control Plane mixins, so keeping this as an explicit runtime configuration seam avoids forcing a new keyword through unrelated constructor contracts.

Before serving a query the Control Plane checks provider state:

1. no checkpoint -> perform a full recovery rebuild;
2. stale checkpoint -> perform a full recovery rebuild;
3. incompatible schema version -> perform one recovery rebuild, then reject the provider if it remains incompatible;
4. fresh compatible checkpoint -> reuse the current derived index without a full rebuild.

If a provider still reports stale after recovery, Search fails closed with an invalid-provider-response error rather than serving data whose completeness cannot be trusted.

A legacy provider that never exposes checkpoints remains safe: checkpointed mode falls back to rebuilding on every query.

## Missed events and reconciliation

Event-driven or write-through integrations may call `mark_search_index_stale(reason)` when they detect conditions such as:

- a missed event sequence;
- a consumer restart without a trusted resume checkpoint;
- a failed indexing transaction whose exact completion state is unknown;
- reconciliation detecting a source/index mismatch;
- a provider restore that cannot prove index completeness.

The next checkpointed query then triggers a full canonical rebuild. This supplies a recovery seam without making the event bus, queue or SearchProvider authoritative.

## Schema evolution

A checkpoint carries the provider's derived index schema version. The Control Plane compares it with `SEARCH_INDEX_SCHEMA_VERSION` before reusing checkpointed state.

A rebuild may migrate a capable provider to the current schema. If the provider still reports an incompatible version after that rebuild, Search returns `unsupported_capability` instead of interpreting an unknown index representation.

Provider upgrades therefore remain explicit and replaceable; canonical resource schemas do not become coupled to one Search backend.

## Relationship to future durable indexing

This slice deliberately does **not** choose Elasticsearch, a vector database, a queue, a broker or any paid service. It provides the synchronization/recovery contract needed by later durable implementations:

```text
canonical resource mutation/event
    -> provider upsert/delete
    -> provider checkpoint advances

missed delivery/reconciliation gap
    -> mark stale
    -> next query/recovery job performs canonical rebuild
    -> fresh checkpoint
```

Batching, durable checkpoint storage and concrete event-consumer wiring remain provider/deployment concerns built on this contract. Semantic/hybrid Search remains optional and unrelated to checkpoint correctness.
