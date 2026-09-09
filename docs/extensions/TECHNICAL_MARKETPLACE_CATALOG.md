# Curated Technical Marketplace Catalog

Issue #638 productizes the generic Registry/Marketplace foundation from issue #81 as a curated ecosystem view of technical components for the AI Multi-Agent Platform.

## Product identity

The default Marketplace experience is for developer and agent infrastructure:

- code intelligence;
- coding agents;
- agent frameworks;
- specification and skill systems;
- memory and context systems;
- evaluation;
- security;
- browser and execution systems;
- inference runtimes;
- retrieval infrastructure;
- model and dataset tooling;
- domain-specific technical candidates where useful.

Ordinary SaaS connectors remain valid canonical Registry items, but they are not the product identity or default home surface of the technical Marketplace.

## Ownership boundary with #81

Issue #81 continues to own the generic distribution engine:

- `RegistryProvider` and filesystem/offline providers;
- canonical `RegistryItem` metadata;
- search/filtering;
- trust, integrity, compatibility and permission preview;
- explicit activation/update;
- durable installation, provenance, pins and history;
- owner-domain routing;
- the graphical `/marketplace` route.

Issue #638 does not create a second package manager, installation lifecycle or trust model. It adds a technical taxonomy, curated reference data, discovery-source gates and a product-facing presentation on top of those contracts.

## Technical taxonomy

The Registry item type remains the canonical distribution type. Technical categories are a second product-facing taxonomy and do not replace it.

Canonical technical categories:

```text
code-intelligence
coding-agent
agent-framework
specification-and-skills
memory-and-context
evaluation
security
browser-and-execution
inference-runtime
retrieval
model-and-dataset-tooling
music-ai
```

A component may carry multiple categories when evidence supports the overlap.

The Marketplace defaults to technical components only but preserves a deliberate `All Registry assets` path for generic #81 browsing.

## Technical metadata without a second Registry schema

Issue #638 intentionally does not fork `RegistryItem`. Product metadata is derived from canonical categories plus a versioned structured-tag vocabulary.

Supported lifecycle values:

```text
discovered
candidate
pilot
adopted
reference
deferred
rejected
deprecated
unknown
```

Supported evaluation values:

```text
required
pending
in-progress
passed
failed
not-required
unknown
```

Supported cost values:

```text
compatible
conditional
incompatible
unknown
```

Supported deployment values:

```text
local
self-hosted
cli
library
service
desktop
extension
browser
hosted
unknown
```

Supported network values:

```text
none
optional
required
unknown
```

Structured tags use these prefixes:

```text
lifecycle:<status>
evaluation:<status>
deployment:<mode>
cost:<status>
network:<status>
provider:<requirement>
resource:<class>
alternative:<registry-item-id>
architecture-ref:<reference>
decision-ref:<reference>
evaluation-ref:<reference>
```

`distribution.technical_catalog` derives a typed backend projection from those tags and fails closed on conflicting/unsupported structured values. The frontend derives the same product presentation from the canonical Registry resource fields. Missing facts render as `unknown`/`not recorded`; they are not fabricated.

## Candidate lifecycle

A Marketplace listing is not an adoption decision.

```text
DISCOVERED / CANDIDATE
        |
        v
source + identity + license + project-status verification
        |
        v
security / cost / resource review
        |
        v
isolated pilot + benchmark / evaluation
        |
        v
recorded decision
        |
        +--> ADOPTED
        +--> REFERENCE
        +--> DEFERRED
        +--> REJECTED
```

Trust and lifecycle are independent. A candidate may be accurately researched while remaining `untrusted` until normal governance supplies stronger evidence.

## Discovery-only external components

An upstream GitHub repository is not automatically a canonical platform package. External candidate entries therefore use Registry schema v2's one-way manual route override:

```json
{
  "distribution_route": "manual"
}
```

The override is fail-closed:

- omitting it preserves #81 routing;
- only `manual` is accepted as an override;
- it can reduce an item to discovery/reference distribution;
- it cannot turn an item into a plugin or portable import;
- manual items can be browsed and inspected but cannot be automatically activated.

No third-party executable source is bundled by the technical reference catalog. The shared `curated-external-component.md` artifact is ScoreSymphony-authored reference material only.

## Bundled offline catalog

The versioned catalog is:

```text
catalogs/technical-components/catalog.json
```

It is consumed by the existing `FilesystemRegistryProvider`, so the technical Marketplace remains deterministic and usable without a hosted Registry.

As of the 2026-09-09 curation pass, the loaded catalog contains at least 47 reviewed entries across the technical taxonomy.

### Code intelligence

- ProjectAtlas
- Graphify
- CodeGraph
- Understand Anything
- Serena
- ast-grep
- Semgrep
- SCIP

SCIP records the current `scip-code/scip` identity rather than the former redirected Sourcegraph namespace.

### Coding agents

- OpenHands
- Aider
- OpenCode
- Goose
- Cline
- Roo Code — archived/reference only
- Plandex
- Gemini CLI
- Codex CLI

OpenCode and Goose record their current post-move repository namespaces. Roo Code is intentionally represented as an archived `reference`, not an active candidate.

### Agent frameworks and platforms

- Pydantic AI
- LangGraph
- smolagents
- Google ADK
- AnythingLLM
- Microsoft Agent Framework
- Agno
- CrewAI
- Dify
- Flowise — archived/reference only
- Letta

Dify's actual modified Apache-2.0 terms are recorded rather than normalized to plain Apache-2.0. Flowise is explicitly archived and records its mixed Apache/commercial licensing instead of being shown as a recommended install.

### Specification and skills

- Spec Kit

### Memory / context / retrieval

- Mem0
- Graphiti
- Qdrant
- AnythingLLM
- Letta

### Evaluation / security

- Promptfoo
- Lighteval
- Inspect AI
- DeepEval
- AgentDojo
- garak
- Semgrep

### Browser and execution

- Browser Use
- Playwright
- Stagehand

### Inference

- llama.cpp
- Ollama
- vLLM
- Text Embeddings Inference (TEI)
- ONNX Runtime
- Transformers.js

### Retrieval / model tooling

- Sentence Transformers
- TEI
- ONNX Runtime
- Transformers.js

### Domain/model tooling

- Transformers.js

For reviewed external entries, official repository identity, license and project status are verified before promotion. Where an upstream release/revision was not explicitly pinned during review, the source revision remains `null`; the Registry `version` is the version of the curated catalog record, not a fabricated upstream release number.

Active external entries remain `manual`, `untrusted`, `candidate` and `evaluation:required`. Archived upstreams can remain discoverable only as explicit `reference` entries and remain manual/non-activatable. `cost:compatible` means the reviewed deployment has no mandatory recurring paid service under the project cost policy; `cost:conditional` means optional/provider choices can introduce costs and must be checked during evaluation.

## Curation queue

Unresolved leads live in:

```text
catalogs/technical-components/CURATION_QUEUE.md
```

The queue is deliberately not loadable by `FilesystemRegistryProvider`. It contains names whose exact identity, current licensing/status or suitability still needs evidence before promotion or explicit rejection/deferment.

This prevents two common catalog failures:

1. silently guessing metadata to make cards look complete;
2. presenting an unreviewed upstream as if it were a trusted/installable component.

The queue also records completed promotion batches so repository moves, archival status and nonstandard licensing decisions stay reviewable.

## Discovery-source seam

`distribution.discovery.RegistryDiscoverySource` is an intentionally weak interface: it can return untrusted discovery leads but has no fetch/install/activate authority.

`curate_discovered_candidate(...)` requires an explicit `CuratedCandidateReview` before a discovery lead can become a canonical Registry item. Promotion always produces an `untrusted`, `manual`, `candidate`, `evaluation:required` item and validates source/license identity consistency.

This is the future seam for sources such as the MCP Registry. A discovery source is never an authorization source or an automatically trusted install feed.

## Marketplace UX

The graphical Marketplace now:

- defaults to the technical-component surface;
- exposes the canonical technical categories as navigation;
- preserves generic Registry item-type filtering;
- presents lifecycle and evaluation badges;
- shows deployment and cost state on cards;
- shows network/provider/resource/alternative/reference metadata in details;
- keeps source, license, provenance and review reference visible before actions;
- renders manual candidates as non-automatically-activatable;
- allows an explicit switch to all generic Registry assets.

This keeps connectors supported without allowing Gmail/Calendar/Slack-style integrations to dominate the product identity.

## Curation rules

Before adding or materially changing a loaded external component entry:

1. resolve the exact official upstream identity;
2. verify license from the official upstream source;
3. verify current project status and record archived/deprecated/maintenance-only state where applicable;
4. record a concrete revision only when it has actually been reviewed;
5. assign canonical Registry item type separately from technical category;
6. keep unknown source/cost/resource/security/compatibility facts explicit;
7. never use popularity/stars as trust or adoption evidence;
8. never bundle arbitrary third-party executable artifacts merely to make a listing installable;
9. use manual distribution until a real platform-owned adapter/package path exists;
10. record lifecycle/evaluation status separately from Registry trust;
11. preserve the project's no-mandatory-recurring-paid-service policy;
12. route any real future installation/integration through existing #81, #15, security, evaluation and owner-domain boundaries.

Repository redirects/moves must be resolved to the current canonical identity before promotion. Non-standard or mixed licensing must be recorded literally enough that the Marketplace does not imply standard SPDX compatibility. Archived projects must not retain active-candidate semantics merely because they were historically listed in architecture notes.

## Review and promotion

A candidate can progress to `pilot`, `adopted`, `reference`, `deferred` or `rejected` only through the project's canonical evaluation/decision/governance systems. Catalog metadata should then reference the relevant evaluation or decision evidence rather than inventing an independent Marketplace lifecycle database.

Archived, license-incompatible or unsuitable projects remain useful historical/reference entries only when that status is explicitly represented and there is a concrete reason to keep them discoverable.
