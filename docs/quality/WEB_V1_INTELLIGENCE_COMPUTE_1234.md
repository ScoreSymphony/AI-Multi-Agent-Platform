# #1234 Intelligence, Tools & Compute Web coverage

This follow-up audit covers only the #1234 Chat 2 slice: Models, Model Providers, Tools,
Capabilities, MCP, Files where not already owned by the merged #1259 slice, Memory, Knowledge,
Search, Nodes, Workers and Compute/Resource surfaces.

The browser boundary remains:

```text
Web -> versioned Control Plane -> canonical domain -> replaceable provider/runtime
```

No browser code in this slice creates a provider-native lifecycle or connects directly to model
servers, MCP servers, Worker transports, storage backends, schedulers or indexes.

## Coverage matrix

| Domain | Maintained routes | Primary reads | Canonical user actions | Required states | Deliberately not a browser lifecycle |
| --- | --- | --- | --- | --- | --- |
| Models | `/models`, `/models/:id` | inventory, detail, health, effective health, capabilities, resource/cost metadata | enable/disable Model; create/version Model Routing Profiles | contextual loading, empty inventory, canonical errors/permission denial, enabled/disabled, health, refresh | Model registration/removal is not exposed as a V1 northbound browser command |
| Model Providers | `/models`, `/models/providers/:id` | provider inventory/detail, operations, limits/resources, availability/health | enable/disable, refresh canonical health | unavailable/degraded notice, command pending, canonical error/permission state, refresh | provider-native configuration, secret handling and provider registration/removal remain behind platform configuration because no public V1 command advertises them |
| Tools / Capabilities | `/tools`, `/tools/:id`, `/tools/providers/:id` | capability/version inventory, provider descriptors, health, safety, side effects, permissions, approvals, schema summaries | create/version Capability Assignments through canonical configuration | contextual loading, empty inventories, partial backend failure, unavailable/degraded provider, canonical errors, refresh | provider registration/removal and direct provider invocation are not exposed by the V1 browser contract |
| MCP | same `/tools` Capability/Provider surface | MCP-backed capabilities appear through canonical Capability Provider descriptors | assignment/policy management available through the same provider-neutral path | unavailable/provider errors are represented by canonical provider state | there is no product MCP-server administration contract; the browser must not connect/configure transports directly |
| Files | `/files`, `/files/:id` | canonical metadata inventory/detail | none added in this slice | inherited #1259 loading/empty/error/deep-link coverage | raw bytes, storage paths and provider-native identity remain intentionally non-Web |
| Memory | `/memory`, `/memory/:id` | scoped inventory/search/detail, provenance, retention, supersession | create, superseding update, short-term promotion, expiry, delete with confirmation | loading, empty, validation, canonical backend/permission errors, explicit mutation pending, refresh/deep link | no provider/index identity becomes Memory truth |
| Knowledge | `/knowledge`, `/knowledge/:id` | source inventory/detail plus query-scoped cited results | register, metadata update, ingest, re-index, detach/delete with confirmation | loading, empty results, validation, canonical backend/permission errors, explicit mutation pending, refresh/deep link | retrieval rows and subordinate discovery documents do not become a second durable lifecycle |
| Search | `/search` | authorized global search with filters, result state and opaque pagination | discovery only | initial search loading, refresh pending, empty authorized results, provider failure, permission-safe filtering, unsupported-mode degradation | Search never owns resource mutation |
| Nodes | `/compute`, `/compute/nodes/:id` | inventory/detail, canonical status, heartbeat, resources, accelerator capacity, runtimes/models/capabilities/workers | drain/undrain; maintenance enable/disable | loading/empty/error, canonical status/offline state, command pending, refresh/deep link | registration/deregistration, transport and scheduler administration are runtime/operator boundaries, not advertised browser commands |
| Workers | `/compute`, `/compute/workers/:id` | inventory/detail, canonical status, node relation, active/concurrency capacity, supported runtimes/models/capabilities | drain/undrain | loading/empty/error, canonical status, command pending, refresh/deep link | Worker registration/deregistration and transport administration are not browser-owned |
| Worker Jobs / resources | `/compute`, `/compute/jobs/:id` | dispatch state, execution status/error, Run/Worker/Artifact references and scheduling requirements | read-only | loading/empty/error and stable deep links | dispatch ownership, reservation and scheduler internals remain server-owned |

## Findings closed in this slice

1. **Search-to-domain reachability.** Global Search already receives canonical File, Memory,
   Knowledge Source, Capability, Capability Provider, Model/Provider, Node and Worker results, but
   several of those result types were rendered without links even though maintained detail routes
   existed. `searchResultPath(...)` now links all of those maintained resource types using encoded
   canonical IDs. Knowledge Documents continue to retain their canonical API reference because the
   product has no separate Knowledge Document detail lifecycle; query-scoped Knowledge retrieval is
   owned by `/knowledge`.
2. **Contextless initial loading.** Models and Tools previously collapsed the complete route to a
   generic Loading state until their first inventory request completed. Their maintained headers,
   metrics and independent sections now remain visible while each canonical section loads.
3. **Provider degradation.** Model Provider and Capability Provider details now present explicit
   degraded/unavailable notices derived only from canonical `health`/`available` fields; no
   provider-private fallback is introduced.
4. **Mutation pending states.** Model/provider commands, Node/Worker administration, Memory
   lifecycle, Knowledge lifecycle/registration and Search refreshes now expose visible live pending
   status rather than relying only on disabled controls.
5. **MCP boundary discoverability.** The Tools surface explicitly states that MCP-backed tools are
   managed through canonical Capability/Capability Provider state and that direct MCP transport
   configuration is not a browser contract.

## Evidence and tests

Focused frontend evidence:

- `frontend/src/pages/SearchPage.test.tsx` covers the newly reachable canonical result routes,
  including URL encoding.
- `frontend/src/pages/IntelligenceComputeCoverage.test.tsx` locks contextual loading coverage for
  Models, Tools/Capabilities/MCP and Compute.
- `frontend/src/api/compute.test.ts` already verifies exact Node/Worker/Worker Job collection reads,
  canonical admin commands, idempotency and client-side reference validation.
- existing Memory/Knowledge tests retain lifecycle/type/query coverage.
- `frontend/src/app/navigation.test.ts` remains the #1259/#1234 primary-route/deep-link parent
  closure guard and is not duplicated here.

Backend evidence used by the audit includes the canonical Memory/Knowledge global Search integration,
Node/Worker Search integration, File Search integration, Capability ResourceServices and the exact
distributed administrative command registration.

## Non-gaps / not applicable

A generic CRUD checklist must not manufacture actions absent from the owning Control Plane. In this
slice there is intentionally no browser button for:

- registering/removing Model Providers or Models when no V1 public command exposes that lifecycle;
- registering/removing Capability Providers or directly configuring MCP transports;
- registering/deregistering Nodes or Workers through the Web;
- mutating Worker Jobs or scheduler reservations;
- File byte/storage-provider administration already excluded by #1259.

Permission denial, authentication failures, provider/backend failures and approval-style canonical
errors continue to use the shared `ErrorState`/Control Plane error presentation rather than
domain-local interpretations.

## Remaining outside this slice

- #589 Research Evidence and #598 Decision Records remain explicitly out of scope.
- #1164 real-browser first-run/E2E evidence is already integrated and closed on current `main`; this slice does not duplicate it.
- #1221 Marketplace expansion is already integrated and closed on current `main`; this slice does not modify Marketplace.
- #1234 remains open until all independent slices and dependency-owned evidence are integrated and
  rechecked on the final combined state.
