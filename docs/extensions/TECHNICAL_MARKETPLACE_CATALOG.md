# Curated Technical Marketplace Catalog

Issue #638 productizes the generic Registry/Marketplace foundation from issue #81 as a curated ecosystem view of technical components for the AI Multi-Agent Platform.

## Product identity

The default Marketplace experience is for developer and agent infrastructure:

- code intelligence;
- coding agents;
- agent frameworks;
- specification and skill systems;
- memory and context systems;
- evaluation and security;
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

A component may carry multiple categories when evidence supports the overlap. The Marketplace defaults to technical components only but preserves an explicit `All Registry assets` path for generic #81 browsing.

## Technical metadata without a second Registry schema

Issue #638 intentionally does not fork `RegistryItem`. Product metadata is derived from canonical categories plus structured tags.

Supported lifecycle values are `discovered`, `candidate`, `pilot`, `adopted`, `reference`, `deferred`, `rejected`, `deprecated` and `unknown`.

Supported evaluation values are `required`, `pending`, `in-progress`, `passed`, `failed`, `not-required` and `unknown`.

Supported cost values are `compatible`, `conditional`, `incompatible` and `unknown`.

Supported deployment values are `local`, `self-hosted`, `cli`, `library`, `service`, `desktop`, `extension`, `browser`, `hosted` and `unknown`.

Supported network values are `none`, `optional`, `required` and `unknown`.

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

`distribution.technical_catalog` derives a typed backend projection from those tags and fails closed on conflicting or unsupported structured values. The frontend derives the same product presentation from canonical Registry resource fields. Missing facts render as `unknown` or `not recorded`; they are not fabricated.

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

The catalog is stored under:

```text
catalogs/technical-components/
```

`catalog.json` is the primary catalog and `catalog.fragment.*.json` files are additive reviewed fragments loaded deterministically in filename order by the existing `FilesystemRegistryProvider`. Provider mismatches, unsupported catalog schema versions and duplicate item/version identities fail closed. Artifact paths remain confined to the catalog root.

As of the 2026-09-10 #638 closeout pass, the loaded technical catalog contains **at least 78 reviewed records** across the canonical technical taxonomy. The exact reviewed inventory and promotion history are maintained in `catalogs/technical-components/CURATION_QUEUE.md` rather than duplicated as a second manually synchronized catalog list in this document.

The reviewed inventory includes the architecture-derived Code Intelligence seed (`ProjectAtlas`, `Graphify`, `CodeGraph`, `Understand Anything`), the wider agent/framework/specification/memory/evaluation/execution/inference/retrieval groups, and the reviewed ScoreSymphony/domain ML candidates. Restricted, archived or policy-incompatible projects are represented as explicit `reference` or `deferred` records rather than silently promoted as active installs.

Notable boundary cases recorded during the final curation passes include:

- Roo Code, Flowise and GSD as archived/reference-only entries;
- Copilot CLI and Claude Code as restricted/deferred service-bound entries;
- MuSViT as deferred because of its non-commercial/share-alike license boundary;
- Multica with its modified Apache terms kept explicit;
- MusicBERT scoped to the `musicbert/` code subproject in `microsoft/muzic`, without extending the repository MIT claim to separately distributed checkpoints or datasets;
- LEGATO as deferred because the practical model path requires the separately licensed and gated `meta-llama/Llama-3.2-11B-Vision` dependency, which is also exposed through canonical `required_models` metadata.

Where an upstream release/revision was not explicitly pinned during review, the source revision remains `null`; Registry `version` is the version of the curated catalog record, not a fabricated upstream release number.

## Curation queue and unresolved leads

Research leads that do not yet have a sufficiently unambiguous current identity, component boundary or licensing/status record remain in:

```text
catalogs/technical-components/CURATION_QUEUE.md
```

The queue is deliberately not loadable by `FilesystemRegistryProvider`. It prevents two common catalog failures:

1. guessing metadata merely to make cards look complete;
2. presenting an unreviewed upstream as if it were trusted or installable.

A lead may be resolved by promotion, explicit reference/deferred/rejected classification, or by documenting that no sufficiently unambiguous current upstream identity exists. Names such as Cursor Agent, Kiro, Open Agent, Fable/Fabel, TurboVec, AgentShield, Colibri and the Hugging Face umbrella remain documented research boundaries rather than guessed catalog identities. Their ambiguity is not missing Registry execution code and is not a reason to weaken #638's verification requirements.

## Discovery-source seam

`distribution.discovery.RegistryDiscoverySource` is intentionally weak: it can return untrusted discovery leads but has no fetch/install/activate authority.

`curate_discovered_candidate(...)` requires an explicit `CuratedCandidateReview` before a discovery lead can become a canonical Registry item. Promotion produces an `untrusted`, `manual`, `candidate`, `evaluation:required` item and validates source/license identity consistency.

This is the future seam for sources such as MCP Registry. A concrete MCP Registry adapter is optional follow-up work; discovery sources are never authorization sources or automatically trusted install feeds.

## Marketplace UX

The graphical Marketplace:

- defaults to the technical-component surface;
- exposes all canonical technical categories as navigation;
- preserves generic Registry item-type/trust/license/publisher/capability/platform filtering;
- presents lifecycle and evaluation badges;
- shows deployment and cost state on cards;
- shows network/provider/resource/alternative/reference metadata in details;
- keeps source, license, provenance and review reference visible before actions;
- renders manual candidates as non-automatically-activatable;
- allows an explicit switch to all generic Registry assets.

Browser regression coverage verifies that the real `MarketplacePage` renders lifecycle/evaluation/deployment/cost state from a technical Registry item, defaults its Registry query to `technical_component=true`, and emits the expected category filter when technical category navigation is used.

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

Repository redirects or moves must be resolved to the current canonical identity before promotion. Non-standard or mixed licensing must be recorded literally enough that the Marketplace does not imply standard SPDX compatibility. Archived projects must not retain active-candidate semantics merely because they were historically listed in architecture notes.

## Review and promotion

A candidate can progress to `pilot`, `adopted`, `reference`, `deferred` or `rejected` only through the project's canonical evaluation/decision/governance systems. Catalog metadata should reference the relevant evaluation or decision evidence rather than inventing an independent Marketplace lifecycle database.

Archived, license-incompatible or unsuitable projects remain useful historical/reference entries only when that status is explicitly represented and there is a concrete reason to keep them discoverable.
