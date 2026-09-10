# Canonical Memory Types

Issue #718 adds a second, provider-neutral dimension to Memory. `MemoryScope` answers **where / for whom** an entry belongs; `MemoryType` answers **what kind of memory** the entry represents. The dimensions are independent and continue to use the same `MemoryProvider` boundary.

## Taxonomy

The canonical semantic types are:

- `episodic`: concrete experiences, observations, or prior outcomes;
- `semantic`: generalized facts, stable knowledge, or learned assertions;
- `procedural`: reusable knowledge about how to perform a task or process;
- `preference`: explicit or reliably established preferences;
- `reflective`: higher-order conclusions derived from other memories or evidence.

`unclassified` is a compatibility value for entries that predate the taxonomy. It is not a recommendation for new Memory creation and deliberately avoids inventing semantics for legacy data.

`MemoryOrigin` remains separate: it records whether an entry is user-authored, agent-derived, or imported. Retention, provenance, classification, supersession, expiry, and authorization also remain independent of Memory Type.

## Lifecycle rules

Promotion between scopes preserves Memory Type. An ordinary update also preserves Memory Type. Changing an entry from, for example, episodic to reflective represents a semantic derivation and therefore requires a new derived Memory entry with provenance back to its source evidence.

The local SQLite provider persists the canonical type directly. External adapters may map it to backend-native tags, graph labels, columns, or metadata, but they must round-trip the canonical value without leaking provider-private taxonomy into platform state.

## Compatibility

Existing SQLite databases are migrated in place by adding a `memory_type` column with the deterministic default `unclassified`. Existing Python call sites that do not yet set a type also receive `MemoryType.UNCLASSIFIED`, preserving compatibility while making the absence of classification explicit.

Portable Memory schema v2 carries both `origin` and `memory_type`. Schema v1 imports remain supported and resolve missing Memory Type to `unclassified`.

Baseline support requires no vector database, embeddings, external model, or paid API.