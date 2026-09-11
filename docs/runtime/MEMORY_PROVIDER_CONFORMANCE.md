# Memory Provider Conformance

The platform exposes two compatible Memory conformance layers:

- `assert_memory_provider_contract(...)` verifies the coarse core `put()` / `get()` seam retained for backward compatibility.
- `assert_scoped_memory_provider_contract(...)` verifies the refined `ai_multi_agent_platform.data.MemoryProvider` contract, including canonical `MemoryType` semantics.

New or refined Memory adapters should run the scoped helper. Passing only the coarse helper is not sufficient evidence that an adapter preserves the canonical scoped-Memory model.

## Adapter test usage

```python
from ai_multi_agent_platform.testing import assert_scoped_memory_provider_contract

await assert_scoped_memory_provider_contract(provider, data_access_context)
```

The helper is backend-neutral. It does not inspect SQLite state, local paths, vector collections, embeddings, provider-native classes or ranking implementation details. No external model, vector database or paid API is required.

## Mandatory baseline semantics

A refined provider must declare and correctly implement the canonical operations for write, get, query, search, supersession, deletion and aggregate expiry. Conformance verifies that:

- `MemoryEntry` identity, scope, owner/creator, provenance, origin, retention, metadata and `memory_type` survive write/read boundaries;
- every canonical `MemoryType` value survives the provider boundary without collapse;
- `MemoryQuery(memory_types=())` remains a scope-only query;
- exact and multi-type filters return all and only the requested canonical types;
- text search honors the same `MemoryQuery.memory_types` filter as ordinary query;
- supersession preserves the historical source type and the replacement's explicitly supplied type;
- expiry and deletion do not reinterpret type before removal;
- provider-private taxonomy remains private and cannot replace `MemoryEntry.memory_type`.

Provider-native tags, classes, collection names, vector namespaces or similar identifiers may be kept in adapter-private state or namespaced adapter metadata, but they are not canonical Memory Type values.

## Optional advertised capabilities

Exact scoped expiry and actor-independent discovery enumeration are optional compatibility seams. The conformance helper follows provider metadata:

- when `expire_entry` is advertised, it must work and preserve canonical type semantics;
- when `expire_entry` is not advertised, calling it must fail with `unsupported_capability`;
- when `list_entries_for_discovery` is advertised, returned entries must retain canonical Memory Type values;
- when discovery is not advertised, calling it must fail with `unsupported_capability`.

An adapter therefore cannot silently advertise a capability and then skip its semantic obligations.

## Reusable non-SQLite fixture

`FakeScopedMemoryProvider` is a deterministic in-memory implementation intended for adapter/conformance tests. It implements the refined scoped contract independently from the SQLite reference provider and also retains the coarse `put()` / `get()` seam. Tests can disable optional exact-expiry or discovery support explicitly to verify degraded compatibility behavior.

`LocalMemoryProvider` and `FakeScopedMemoryProvider` are both run through the same scoped conformance suite. This is the baseline proof that the contract is replaceable rather than an accidental property of the local SQLite implementation.
