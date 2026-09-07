# Usage and Resource Discovery in Global Search

This document records the Issue #45 integration of the completed Issue #76 accounting domain into the platform-wide Search layer.

The general Search invariants in `docs/SEARCH.md` continue to apply: Search is derived and non-authoritative, canonical IDs remain primary identity, provider/private storage is not queried directly by Search, and authorization is applied before caller-visible counts, snippets, cursors or exact-ID results are calculated.

## Searchable accounting resources

The canonical #76 Control Plane exposes three accounting collections:

- `usage-records` — attributable raw measurements;
- `usage-aggregates` — current metric/unit aggregate views;
- `usage-budgets` — canonical configured budget state.

Global Search intentionally indexes only:

- `usage-aggregate` resources from `usage-aggregates`;
- `usage-budget` resources from `usage-budgets`.

Raw `usage-record` resources opt out of global Search with `search_indexable = False`.

Raw measurements can be high-volume and can carry operationally sensitive provider/source/provenance information. They remain inspectable through their owning canonical #76 API when authorized, but are not promoted into the global discovery index.

## Canonical rebuild enumeration

Ordinary accounting ResourceService list/read operations remain actor/owner filtered. Search must nevertheless be able to rebuild a complete derived index so later authorization can decide which candidates a caller may discover.

Registered ResourceServices may therefore optionally implement:

```python
async def list_search_resources(self) -> tuple[dict[str, JsonValue], ...]: ...
```

This is an internal canonical rebuild seam, not a public bypass route.

Rules:

1. The owning ResourceService remains the schema and privacy authority.
2. Search never reaches into the domain store/provider to enumerate private state directly.
3. The enumeration returns the same safe canonical northbound resource projection used by the domain.
4. Every candidate still passes the registered collection's normal `<singular>:list` authorization action before becoming caller-visible.
5. A ResourceService may opt out of global Search completely with `search_indexable = False`.
6. Services that define neither option continue to use the existing registered-domain `list_resources(...)` rebuild path.

This seam is reusable for later actor-filtered canonical domains without changing Search identity semantics or making Search their schema authority.

## Safe Usage Search projection

For `usage-aggregate` and `usage-budget`, Search may use safe discovery vocabulary such as:

- canonical resource ID and type;
- `metric_type`;
- `unit`;
- aggregation mode;
- canonical owner scope;
- Project/Workspace scope where present;
- budget scope type and scope ID;
- budget kind;
- budget action;
- budget window mode;
- threshold level where present.

Search extracts owner/Project/Workspace scope from the canonical accounting projection, including nested `scope` metadata for aggregates and `scope_type`/`scope_id` for budgets. These fields are used both for filtering and for per-result authorization.

Useful display labels are derived without creating a second accounting model, for example:

- `<metric_type> usage (<unit>)` for an aggregate;
- `<metric_type> budget for <scope_type> <scope_id>` for a budget.

The canonical API references remain:

- `/api/v1/usage-aggregates/{id}`;
- `/api/v1/usage-budgets/{id}`.

## Data deliberately excluded from Search text

Global Search does not promote the following accounting values into keywords or snippets merely because they exist on the richer #76 resource:

- raw measurement quantity;
- aggregate total;
- budget limit;
- consumed/remaining/fraction values;
- raw cost amounts or currencies;
- precision/confidence values;
- provider/source details from raw UsageRecords;
- arbitrary accounting provenance payloads;
- trend point values and quality-count payloads.

This keeps Global Search useful for discovering the relevant accounting resource without turning it into a broad operational-data exfiltration surface.

## Authorization and non-disclosure

`usage-aggregate` Search results reuse `usage-aggregate:list`.

`usage-budget` Search results reuse `usage-budget:list`.

The candidate's canonical owner and Project scope are supplied to the authorization decision. Unauthorized resources are removed before:

- result totals;
- snippets;
- cursors;
- exact-ID results;
- Project-filter results.

A rebuild may therefore contain resources for multiple owners while a caller still sees only the resources permitted by canonical authorization. Knowing another owner's metric name, Project ID or Budget ID must not reveal that resource through Search.

## Accounting authority remains in #76

Search does not calculate accounting truth, enforce budgets, emit thresholds or mutate usage state.

The owning #76 accounting layer remains authoritative for:

- UsageRecord persistence;
- aggregation semantics;
- measurement quality;
- budget configuration and versions;
- consumed/remaining state;
- threshold evaluation;
- policy inputs/events.

Search only provides an authorization-safe discovery path to the canonical aggregate and budget resources.

## Baseline and cost constraints

This integration uses the existing local/replaceable SearchProvider boundary and canonical #76 services. It requires no vector database, embeddings, paid search API or other recurring external service.

## Tests

The Issue #45/#76 integration tests prove:

- aggregate and budget discovery by safe metadata;
- Project filtering and exact canonical Budget ID lookup;
- complete rebuild across multiple owners;
- authorization-safe suppression of another owner's resources, counts and exact IDs;
- raw UsageRecord exclusion;
- sensitive numeric/provider/provenance values are not globally searchable;
- Search invokes accounting list authorization for aggregate/budget candidates only.
