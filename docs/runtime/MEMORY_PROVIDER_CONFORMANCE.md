# Memory Provider Conformance

The platform exposes two compatible Memory conformance layers:

- `assert_memory_provider_contract(...)` verifies the coarse core `put()` / `get()` seam retained for backward compatibility.
- `assert_scoped_memory_provider_contract(...)` verifies the refined `ai_multi_agent_platform.data.MemoryProvider` contract, including canonical `MemoryType` semantics and canonical filter composition.

New or refined Memory adapters should run the scoped helper. Passing only the coarse helper is not sufficient evidence that an adapter preserves the canonical scoped-Memory model.

## Adapter test usage

```python
from ai_multi_agent_platform.testing import assert_scoped_memory_provider_contract

await assert_scoped_memory_provider_contract(provider, data_access_context)
```

The package-level helper is the canonical adapter-facing conformance entry point. It composes the baseline refined lifecycle/type checks with the stricter #745 scope/owner/filter-order checks so adapters do not need to assemble multiple suites themselves.

The helper is backend-neutral. It does not inspect SQLite state, local paths, vector collections, embeddings, provider-native classes or ranking implementation details. No external model, vector database or paid API is required.

## Mandatory baseline semantics

A refined provider must declare and correctly implement the canonical operations for write, get, query, search, supersession, deletion and aggregate expiry. Conformance verifies that:

- `MemoryEntry` identity, scope, owner/creator, provenance, origin, retention, metadata and `memory_type` survive write/read boundaries;
- every canonical `MemoryType` value survives the provider boundary without collapse;
- `MemoryQuery(memory_types=())` remains a scope-only query;
- exact and multi-type filters return all and only the requested canonical types;
- Scope/Scope ID, `owner_ref` and Memory Type remain independent filtering dimensions;
- adding a Memory Type filter cannot broaden scope or owner visibility;
- the canonical `limit` is applied only after scope, owner, type, expiry and supersession filters;
- text search preserves the same canonical query filters, including `memory_types`, scope/owner visibility and limit semantics;
- supersession preserves the historical source type and the replacement's explicitly supplied type;
- expiry and deletion do not reinterpret type before removal;
- provider-private taxonomy remains private and cannot replace `MemoryEntry.memory_type`.

Provider-native tags, classes, collection names, vector namespaces or similar identifiers may be kept in adapter-private state or namespaced adapter metadata, but they are not canonical Memory Type values.

## Authorization boundary

Memory Type is a semantic retrieval dimension, not an authorization grant. Scope/ownership authorization remains independent from Type.

The reusable provider helper verifies backend-neutral scope/owner/type composition. Separate #15 regression coverage verifies that `AuthorizedDataMemoryProvider` performs authorization before delegating and forwards the complete canonical `MemoryQuery` unchanged, including `memory_types`, to both ordinary query and text search. A type-filtered request therefore cannot bypass the authorization wrapper, and a wrapper cannot silently drop the requested canonical type filter.

## Optional advertised capabilities

Exact scoped expiry and actor-independent discovery enumeration are optional compatibility seams. The conformance helper follows provider metadata:

- when `expire_entry` is advertised, it must work and preserve canonical type semantics;
- when `expire_entry` is not advertised, calling it must fail with `unsupported_capability`;
- when `list_entries_for_discovery` is advertised, returned entries must retain canonical Memory Type values;
- when discovery is not advertised, calling it must fail with `unsupported_capability`.

An adapter therefore cannot silently advertise a capability and then skip its semantic obligations.

## Reusable non-SQLite fixture

`FakeScopedMemoryProvider` is a deterministic in-memory implementation intended for adapter/conformance tests. It implements the refined scoped contract independently from the SQLite reference provider and also retains the coarse `put()` / `get()` seam. Tests can disable optional exact-expiry or discovery support explicitly to verify degraded compatibility behavior.

`LocalMemoryProvider` and `FakeScopedMemoryProvider` are both run through the same scoped conformance suite. This is the baseline proof that the contract is replaceable rather than an accidental property of the local SQLite implementation. Deliberately broken fixtures additionally prove that conformance rejects ignored owner/type filters and providers that apply result limits before canonical filtering.
