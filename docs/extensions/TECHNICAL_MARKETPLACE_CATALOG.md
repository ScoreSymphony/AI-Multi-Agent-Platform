# Curated Technical Marketplace Catalog

Issue #638 productizes the generic Registry/Marketplace foundation from issue #81 as a curated ecosystem view of technical components for the AI Multi-Agent Platform.

## Product identity

The technical catalog is primarily for developer and agent infrastructure:

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

Ordinary SaaS connectors remain valid Registry items, but they are not the product identity or default curation focus of this catalog.

## Taxonomy

The Registry item type remains the canonical distribution type from issue #81. Technical categories are a second, product-facing taxonomy and must not replace or redefine those item types.

Initial category vocabulary:

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

A component may carry more than one category when that is supported by evidence. Categories should describe capabilities rather than a vendor-specific implementation detail.

## Candidate lifecycle

A Marketplace listing is not an adoption decision.

External projects should move through the platform's normal component governance, conceptually:

```text
DISCOVERED / CANDIDATE
        |
        v
source + identity + license verification
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

Trust state and lifecycle state are different concepts. A technically interesting candidate may remain `untrusted` in Registry metadata until project governance has supplied stronger evidence.

Unknown source, cost, resource, security or compatibility facts must remain unknown. Catalog curation must not invent values merely to make a card appear complete.

## Discovery-only external components

The generic issue #81 Registry originally inferred the distribution route from the item type. That is correct for canonical portable platform assets, but it is too strong for an external technical candidate.

For example, Graphify is semantically a `tool`, yet a Marketplace listing must not imply that the upstream GitHub repository is already a canonical portable Tool package.

Registry metadata therefore supports the optional field:

```json
{
  "distribution_route": "manual"
}
```

The override is intentionally one-way and fail-closed:

- omitting it preserves all existing issue #81 routing behavior;
- only `manual` is accepted as an override;
- it can reduce an item to reference/manual distribution;
- it cannot turn another item into a plugin or portable import;
- manual items remain visible and inspectable but cannot be activated automatically.

This keeps the existing owner-domain activation boundaries intact while allowing an accurate catalog of candidates that still need evaluation or a future adapter/package.

## Bundled reference catalog

The first reference catalog lives at:

```text
catalogs/technical-components/catalog.json
```

It is consumable by the existing `FilesystemRegistryProvider` and therefore remains compatible with offline/self-hosted deployments.

The first slice contains the architecture-derived Code Intelligence candidates:

- ProjectAtlas — P1 agent-first repository intelligence candidate;
- Graphify — graph/architecture specialist candidate;
- CodeGraph — structural comparison candidate;
- Understand Anything — semantic/domain/impact specialist candidate.

Each entry is a `tool` categorized as `code-intelligence`, marked as a candidate through tags/reference metadata, uses manual distribution and remains untrusted until later governance says otherwise.

The accompanying files under `catalogs/technical-components/artifacts/` are ScoreSymphony-authored reference cards only. They do not vendor, copy, bundle or execute third-party project source.

## Curation rules

Before adding or materially updating an external component entry:

1. Resolve the exact upstream project identity.
2. Prefer the official upstream repository or project site as provenance.
3. Verify the license from the upstream project or package metadata.
4. Record a concrete release/tag/revision when a stable one is known.
5. Mark archived, maintenance-only, deprecated or rejected projects accurately.
6. Do not equate popularity, stars or download count with trust or adoption.
7. Do not bundle third-party executable artifacts merely to make a Marketplace entry installable.
8. Use manual/discovery-only distribution until a canonical platform package or explicit adapter integration exists.
9. Preserve the no-mandatory-paid-service architecture and make cost uncertainty explicit.
10. Route any real future installation/integration through the platform's existing security, provenance, evaluation and owner-domain lifecycle.

## Future discovery sources

External registries such as the MCP Registry may later be used as discovery inputs. Imported discovery metadata must remain untrusted until it passes normal curation and promotion gates. An external catalog is not an authorization source and must not become an automatic install feed.
