# Repository-Intelligence Candidate Audit

> Verification snapshot: 2026-09-07

This document records the first fresh upstream audit for issue #502. It deliberately replaces
name-only assumptions from older architecture notes with current repositories, licenses, release or
maintenance evidence, local-cost paths and platform-fit risks.

This is **not** an adoption decision. A candidate reaches the platform only after the security,
resource, correctness and comparative-evaluation gates in `REPOSITORY_INTELLIGENCE.md` pass.

## Audit rules

For each candidate the audit verifies, where available:

- current canonical/redirected upstream repository;
- license from the upstream repository, not a search snippet;
- recent maintenance/release evidence;
- local/offline or no-paid-provider path;
- capability overlap with the #502 taxonomy;
- repository/Workspace lifecycle conflicts;
- persistent-state and write-scope concerns;
- model/network/secret requirements;
- a bounded pilot shape that cannot become canonical architecture accidentally.

Marketing benchmark claims are recorded only as upstream claims and are never accepted as platform
measurement.

## Snapshot

| Candidate | Current upstream | License | Maintenance evidence | No-paid reference path | Preliminary disposition |
| --- | --- | --- | --- | --- | --- |
| ProjectAtlas | `styler-ai/ProjectAtlas` | MIT | active commits on 2026-09-06; README advertises stable `v0.4.5` | yes; local Rust CLI/MCP, no hosted index or credentials required | **pilot first** |
| CodeGraph | `codegraph-ai/CodeGraph` | Apache-2.0 | latest release `v0.20.1`, 2026-08-10 | yes; local `--graph-only` path needs no API key; embeddings are optional | pilot after baseline/ProjectAtlas |
| Graphify | `Graphify-Labs/graphify` | Apache-2.0 | release commit `0.9.55`, 2026-09-05 | yes for code maps; local tree-sitter path uses no LLM | specialist pilot candidate |
| Understand Anything | `Egonex-AI/Understand-Anything` | MIT | active commits on 2026-09-06 | possible with subscription/local model, but full initial analysis is model-heavy | experimental/high-level specialist |

## 1. ProjectAtlas

### Verified upstream state

Current upstream: `styler-ai/ProjectAtlas`.

Evidence captured at audit time:

- repository is public and not archived;
- `LICENSE` is MIT;
- recent upstream commit observed at `d8abe44a46680e5994e53d22e3bce757ae063b03`
  on 2026-09-06;
- current README advertises stable `v0.4.5`;
- implementation is Rust-native and exposes CLI plus MCP surfaces;
- normal project state is a project-local SQLite database under
  `.projectatlas/projectatlas.db`;
- architecture documentation also describes explicit startup `--db` as an isolated binding;
- upstream explicitly states that Git remains worktree lifecycle authority.

The README describes local repository maps containing files, purposes, deterministic summaries,
symbols, graph relationships, searchable text, health data, exact source slices and token
telemetry. It states that no hosted index or credentials are required.

### Platform fit

ProjectAtlas is the closest match to the intended #502 context funnel because it already has:

- bounded repository orientation;
- symbols and resolved graph relationships;
- exact source slices;
- freshness/watch behavior;
- MCP integration;
- local persistent indexing;
- explicit source-vs-derived navigation semantics.

It should therefore be the **first third-party pilot**, subject to the gates below.

### Pilot restrictions

The platform must not expose ProjectAtlas as a second Repository/Workspace owner.

The first pilot should:

1. pin one exact upstream release/commit and verify release checksum/provenance;
2. run against a platform-selected repository/Workspace only;
3. bind the database/cache outside the repository write scope where the upstream `--db` contract
   safely permits it;
4. expose only a read-only allowlist such as map/summary/symbol/relation/search/source-slice/health;
5. deny purpose mutation, project initialization that mutates source scope, worktree registration or
   any other authored/lifecycle operation;
6. deny network egress after installation unless a measured operation genuinely requires it;
7. compare output revision/freshness against canonical #82/#37 evidence before returning results;
8. destroy the pilot cache without affecting repository or Workspace state.

The default `.projectatlas/` placement is not sufficient evidence for the #502 requirement that
provider index writes can be isolated from repository writes. The pilot must prove the external
cache binding rather than assume it.

### Open questions

- exact supported read-only MCP/CLI allowlist for the pinned version;
- behavior of `--db` under a read-only source mount and dirty Workspace;
- whether every exact-slice/symbol result can be tied to the canonical revision/Workspace state;
- rebuild/update CPU, RAM, disk, WAL and latency on platform fixtures;
- safe process sandbox/resource-limit profile.

## 2. CodeGraph

### Verified upstream state

Current upstream: `codegraph-ai/CodeGraph`.

Evidence captured at audit time:

- repository is public and not archived;
- `LICENSE` is Apache-2.0;
- latest GitHub release observed is `v0.20.1`, published 2026-08-10;
- release binaries publish matching SHA-256 assets;
- README describes a local engine with 42 community MCP tools and tree-sitter parsing across
  38 languages;
- VS Code/JetBrains clients can download a checksummed native engine after explicit user action;
- `--graph-only` skips embeddings and is documented for CI/one-shot structural queries without API
  keys;
- semantic embeddings are optional and can use local models, with a documented memory gate around
  1.5 GB for the ONNX path;
- persistent project memory and documentation stores are also offered upstream.

### Platform fit

Strong areas include:

- symbols/callers/callees;
- dependency and call graphs;
- impact analysis;
- related tests and entry points;
- one-shot PR context;
- local graph-only operation;
- optional semantic search.

The community graph-only profile is the only suitable initial pilot shape. Paid/pro surfaces must
not become a reference-path dependency.

### Pilot restrictions

The first CodeGraph pilot should:

- use only community `--graph-only` structural tools;
- disable/exclude upstream persistent-memory features because platform Memory remains canonical;
- avoid upstream documentation mutation/generation as a platform truth source;
- pin the engine release and verify the published SHA-256 before execution;
- isolate engine/index state from repository writes;
- deny outbound network after installation unless separately approved;
- measure the graph-only resource profile before testing embeddings;
- treat any Pro-only capability as unavailable, not as a reason to add a paid dependency.

### Open questions

- exact index location/override and cleanup semantics for a sandboxed platform plugin;
- opt-out/default behavior of any telemetry in the pinned community engine;
- dirty-Workspace incremental freshness guarantees;
- source-slice/revision provenance shape;
- practical overlap with ProjectAtlas after both are measured.

## 3. Graphify

### Provenance correction

The older `Kamen-Hashimov/graphify` repository is not the current candidate to evaluate. Its README
identifies `safishamsi/graphify` as the official upstream; that GitHub repository now redirects to
`Graphify-Labs/graphify`.

The audit therefore treats **`Graphify-Labs/graphify`** as the current upstream.

### Verified upstream state

Evidence captured at audit time:

- repository is public and not archived;
- default branch is currently `v8`;
- `LICENSE` is Apache-2.0;
- recent release commit `c9f99018774e2e0380e9f65b3959944559a0d5f6` advertises `0.9.55`
  and was committed 2026-09-05;
- current README states that code maps are free and fully local;
- code structure is parsed with tree-sitter without an LLM and nothing from that code pass needs to
  leave the machine;
- docs/PDFs/images/video can optionally use the assistant model or a configured API key for a
  semantic pass;
- output includes `graph.json`, a report and an interactive graph;
- edges distinguish extracted from inferred relationships;
- upstream also advertises a hosted/early-access platform, which is outside the reference path.

### Platform fit

Graphify is attractive as a **specialist structural-graph provider** because its code-only path is
local, deterministic at the parsing stage and does not require embeddings or a paid model API.

Its useful #502 overlap is primarily:

- symbol/relationship graph;
- imports/calls/inheritance;
- graph traversal/path queries;
- architectural communities;
- rationale/document-reference extraction;
- persistent graph reuse.

### Pilot restrictions

The platform pilot should use **code-only/local mode**. It must not:

- activate hosted Graphify services;
- require an external API key;
- send repository content to a configured semantic backend;
- install always-on agent hooks globally;
- write generated graph/report files into the canonical repository unless an explicit isolated
  Workspace policy allows that side effect.

The pilot must first prove that graph state/output can be redirected to provider-owned state or run
inside an isolated disposable mount. Default `graphify-out/` behavior is not enough for the #502
write-scope invariant.

### Open questions

- stable machine-facing/MCP surface for bounded provider calls in the pinned release;
- external output/cache-directory controls;
- dirty-Workspace incremental freshness;
- exact revision provenance on graph nodes/edges;
- resource behavior on large repositories;
- value beyond ProjectAtlas rather than duplicate always-on indexing.

## 4. Understand Anything

### Provenance correction

Requests for the older `Lum1104/Understand-Anything` path currently resolve to
`Egonex-AI/Understand-Anything`. The current README states that the project is now an Egonex
open-source project and was originally created by Lum1104.

### Verified upstream state

Evidence captured at audit time:

- repository is public and not archived;
- `LICENSE` is MIT;
- recent upstream commit observed at `07edf82a04371b6f69779b067bdc8a1a8753a9db`
  on 2026-09-06;
- current README documents Codex and many other coding-agent integrations;
- analysis builds a persistent knowledge graph with file/function/class/dependency information;
- features include semantic/fuzzy search, domain/business views, diff impact analysis, guided tours
  and architecture-layer visualization;
- initial analysis is explicitly documented as potentially consuming significant model tokens;
- subsequent analysis is incremental;
- upstream documents local-model operation as an alternative to hosted model use;
- default graph state is written under `.ua/` (with legacy directory compatibility).

### Platform fit

Understand Anything is more valuable as a **higher-level semantic/domain specialist** than as the
first repository-intelligence provider. Its strongest differentiators are domain views, guided
architecture explanations and model-assisted semantic knowledge extraction.

Those outputs are also less deterministic than the structural baseline and must be clearly marked
as advisory/derived.

### Pilot restrictions

A future pilot should:

- run only after a structural baseline/provider has been measured;
- use an already permitted subscription/local model path rather than introducing a paid API;
- isolate `.ua` state from canonical repository writes;
- preserve source/revision evidence separately from model-generated explanations;
- keep generated domain/summary content advisory unless deliberately reviewed and promoted through
  an explicit authored-metadata lifecycle;
- cap model/resource consumption during initial indexing.

### Open questions

- clean non-interactive provider/API boundary suitable for #20 packaging rather than only agent
  skill orchestration;
- exact revision/source provenance for every returned graph/slice result;
- separation between deterministic static-analysis output and model-generated interpretation;
- external state-directory configuration;
- measured benefit relative to cheaper structural providers.

## Pilot order

The current evidence supports this order:

1. **ProjectAtlas read-only pilot** — closest match to the context funnel and exact source-slice
   model; prove external DB isolation first.
2. **Graphify code-only pilot** — evaluate as a non-embedding structural graph specialist if its
   output can be isolated cleanly.
3. **CodeGraph graph-only pilot** — evaluate its richer structural/impact tool surface and native
   engine cost after the first two establish baseline measurements.
4. **Understand Anything specialist experiment** — evaluate only for semantic/domain value that the
   structural providers do not provide.

This is deliberately an evaluation order, not an adoption order. If ProjectAtlas already covers the
measured structural needs, Graphify or CodeGraph may be rejected as redundant. Conversely, a
specialist can remain on-demand without becoming an always-on indexer.

## Next evidence required for #502

Before any candidate is packaged as a platform plugin:

1. implement the policy-enforced production snapshot/Workspace binding for the deterministic
   baseline;
2. add the common benchmark/evaluation harness so every candidate is compared against identical
   repository revisions and tasks;
3. perform an isolated ProjectAtlas pilot at one exact pinned version;
4. record installer/runtime checksums, permissions, network behavior, cache paths and resource
   bounds;
5. exercise clean/dirty Workspace freshness and stale-provider fallback;
6. only then decide whether a second structural provider adds enough unique value to justify its
   maintenance and resource surface.
