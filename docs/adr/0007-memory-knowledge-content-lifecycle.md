# ADR 0007: Canonical Memory and Knowledge content lifecycle

- Status: Accepted
- Date: 2026-09-04
- Issue: #251

## Context

ADR 0002 separates canonical lifecycle state, Files, scoped Memory and Knowledge and defines replaceable provider boundaries. Issue #13 implemented those provider contracts and local reference providers, but northbound clients still lack a stable content-management contract. The current Control Plane exposes data-provider inventory and File metadata, while Memory and Knowledge content remain provider-facing operations.

Issue #251 requires canonical content lifecycle APIs without allowing provider-private vector/index/database identifiers to become client identities. It also requires explicit Memory origin semantics, Organization-scoped Memory, non-disclosure before authorization, deterministic Knowledge source management and explicit degraded provider state.

## Decision

1. The existing `ai_multi_agent_platform.data` provider contracts remain the replaceable southbound boundary. Issue #251 extends them where canonical content inspection is missing; it does not create a second storage architecture.
2. Memory entries gain an explicit canonical origin classification: `user-authored`, `agent-derived` or `imported`.
3. The existing `workspace` Memory scope continues to represent the Project/Workspace scope from ADR 0002. A distinct `project` alias is not introduced.
4. Memory gains an explicit `organization` scope. Organization scope IDs remain canonical authorization/ownership references and are not backend IDs.
5. Chat messages, Events, Files and Workspace snapshots never become Memory implicitly. Promotion from such material is an explicit Memory lifecycle operation with provenance.
6. Knowledge sources remain the canonical management identity. Documents and index references are subordinate canonical references; provider-private index/vector IDs remain adapter metadata only.
7. The refined Knowledge provider contract exposes canonical source list/read operations so the Control Plane never calls implementation-private repository helpers.
8. Provider indexing/search availability is reported separately from source canonical metadata. Provider failure may degrade retrieval/index state but must not replace or erase canonical source identity/provenance.
9. `/api/v1/memory` and `/api/v1/knowledge` are platform-owned Control Plane collections. Mutations use the existing versioned idempotent command boundary rather than provider-specific HTTP routes.
10. Authorization is enforced before content, snippets, counts or existence-sensitive results are returned. Inaccessible and absent content use non-disclosing behavior at the northbound boundary.

## Consequences

### Positive

- #236 can consume Memory and Knowledge without direct provider calls;
- provider replacement preserves canonical IDs and provenance;
- Memory origin and Organization scope become testable domain semantics rather than UI conventions;
- Knowledge list/detail no longer depends on private methods of the SQLite reference implementation;
- Chat/Event history cannot silently turn into mutable Memory.

### Costs

- the SQLite Memory schema needs an additive origin migration;
- authorization wrappers and provider contract tests must cover the new source inspection methods;
- clients must use explicit promotion when converting other canonical material into Memory;
- provider degradation requires explicit status/error handling instead of treating an index as the source of truth.

## Alternatives considered

### Store Memory origin only in generic metadata

Rejected. Origin is required lifecycle semantics and must not be an optional convention that providers or clients can omit silently.

### Add a separate `project` Memory scope beside `workspace`

Rejected. ADR 0002 already defines `workspace/project` as one canonical scope. A second equivalent scope would create ambiguous ownership and query behavior.

### Let the Control Plane call `LocalKnowledgeProvider._get_source()` / `_list_sources()`

Rejected. That would bind a canonical northbound API to one reference implementation and violate provider replaceability.

### Make a vector/search index the canonical Knowledge resource

Rejected. Search/index state is derived and replaceable; Knowledge source identity and provenance remain canonical independently of any retrieval backend.

## Affected issues and contracts

- #251 — owner of this decision and implementation
- #13 — extends, but does not replace, its data/provider boundaries
- #15 / #36 — authorization and authenticated actor context
- #37 — Project/Workspace scope remains unchanged
- #45 — search may consume discoverable metadata but not retrieval truth
- #72 — explicit promotion path for selected conversational material
- #236 — consumes the stable `/memory` and `/knowledge` surfaces
