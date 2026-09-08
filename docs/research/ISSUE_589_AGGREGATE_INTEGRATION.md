# Issue #589 aggregate integration map

This document is the handoff for consolidating all active #589 branches into the later unified
integration branch. Branch-local CI/CodeQL/smoke status is intentionally not part of this handoff;
validation is deferred until the aggregate branch exists.

## Baseline already in `main`

The current `main` baseline already owns the canonical Research Evidence core:

- ResearchItem / Source / SourceObservation / Claim / Evidence models;
- durable `SqliteResearchRepository`;
- freshness and source-change semantics;
- canonical #15 authorization hooks;
- canonical #86 Research verification bridge;
- Repository Intelligence provenance ingestion;
- Research -> Planning and Research -> Knowledge/Memory bridges;
- untrusted Research execution profile;
- Research Control Plane resources and mutation commands.

The active branches below extend that baseline; none should become a second authority for Task/Run,
Authorization, Verification, Search, Evaluation, Decision, Files/Artifacts or Portability.

## Active #589 branches

### 1. `feat/589-research-search` — PR #647

Adds the derived global Search projection for Research Items, Sources, SourceObservations, Claims and
Evidence. Search documents are privacy-minimized and every returned result is re-authorized against
the canonical Research owner before disclosure.

This branch is a dependency of the single-node composition branch.

### 2. `feat/589-research-evaluation` — PR #654

Adds deterministic #19 Research-quality evaluation plus the standard #77 Research Team integration
coverage. The evaluation measures citation coverage, current/stale/unavailable/unverifiable
Evidence, Claim support/dispute state, contradiction count, exact SourceObservation binding and #86
Verification coverage without a paid/model judge dependency.

This branch is independent of Search and Governance, but it modifies shared Evaluation exports and
should be integrated before final formatting/validation.

### 3. `feat/589-research-single-node` — PR #660

Stacked on `feat/589-research-search`. Adds an additive `compose_single_node_research(...)` seam that
binds durable `research.sqlite3`, the existing #15 `AuthorizationGate` and Search-aware Research
Control Plane registration.

The aggregate branch should fold this seam into the standard `SingleNodeDeployment` composition
once all concurrent deployment changes have been combined.

### 4. `feat/589-research-governance-completion` — PR #661

Adds:

- Research -> #598 Decision Record provenance;
- provenance-preserving Research bundle export/import;
- local #86 revalidation for imported Verification bindings;
- #79 `ResearchBundlePortableCodec` with historical-preserve semantics;
- prepared Decision/Portability/E2E acceptance coverage.

This branch is independent of Search/Evaluation at the code level.

## Recommended aggregate order

1. Start from the then-current integration baseline, not from today's `main` SHA.
2. Bring in `feat/589-research-search`.
3. Bring in `feat/589-research-single-node` after Search.
4. Bring in `feat/589-research-evaluation`.
5. Bring in `feat/589-research-governance-completion`.
6. Resolve shared exports/composition once, after all branch content is present.

The order of steps 4 and 5 can be swapped; their functional surfaces are independent.

## Central fold-ins to perform only on the aggregate branch

### Standard single-node deployment

Fold `compose_single_node_research(...)` into `build_single_node_deployment(...)`:

- expose long-lived `research: ResearchService` on `SingleNodeDeployment`;
- construct it from `database_dir / "research.sqlite3"` after `approval_gate` exists;
- register `register_searchable_research_control_plane(control_plane, research)`;
- return the same service on the deployment object.

### #79 portability registry

Register `ResearchBundlePortableCodec` in the shared serializer registry/composition. Do not add a
generic mutation handler with no-op rollback. The owner-domain `import_research_bundle(...)` must
remain responsible for exact graph validation and local #86 trust validation unless a real
transaction/rollback seam is added to Research persistence.

### #19 evaluation product policy

Decide once whether `canonical_research_quality_suite(...)` is:

- a built-in deployment-owned evaluation asset, or
- an explicit/versioned opt-in Research suite.

Do not duplicate Research quality thresholds in deployment code or workflow YAML.

### Public package exports

Reconcile final `research`, `evaluation` and `portability` package exports after all active branches
are present. Avoid branch-specific compatibility aliases if the aggregate package can expose one
canonical name directly.

## Aggregate acceptance path

The unified branch should exercise the complete governed chain:

1. create scoped ResearchItem;
2. acquire/record Source and immutable SourceObservation provenance;
3. create Claims and Evidence;
4. prove unsupported/stale/disputed Research cannot drive action;
5. complete exact independent #86 Verification;
6. use verified Research as Planning and #598 Decision evidence;
7. verify downstream provenance retains ResearchItem/Claim/Evidence/Verification identity;
8. rebuild Search and prove owner isolation;
9. export/import Research provenance and reject untrusted imported Verification bindings;
10. restart the standard single-node deployment and recover the same Research state;
11. run deterministic #19 Research-quality evaluation and Research Team role-separation coverage.

Only after this combined path is present should the normal repository-wide CI, CodeQL, smoke,
regression and merge-readiness checks be used as the final gate.

## Deferred by design

During this preparation window:

- do not merge #647, #654, #660 or #661 independently;
- do not change branches solely to satisfy isolated CI runs;
- do not close #589;
- do not create a second Research-specific Search, Verification, Decision or execution authority.
