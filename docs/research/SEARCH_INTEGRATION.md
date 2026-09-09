# Research Search integration

Issue #589 integrates canonical Research Evidence with the existing global Search subsystem from
#45. Research does not own a second index, query language or provider runtime.

## Composition

Use `register_searchable_research_control_plane(control_plane, research)` when the composed
Control Plane includes global Search. It installs the normal Research commands and replaces only
the registered Research read services with Search-aware variants.

The Search-aware services implement the existing internal `list_search_resources()` rebuild seam
and `search_result_allowed(...)` authorization seam. The global Search provider remains derived
state and is reconstructable from the canonical Research repository.

## Privacy boundary

Search rebuilds must enumerate canonical resources without inventing a privileged synthetic actor.
For that reason Research exposes separate, privacy-minimized Search projections rather than
calling actor-filtered `list_resources(...)` with a fake identity.

The derived Search documents intentionally omit:

- full Source locators and URLs;
- source content, snapshot and excerpt digests;
- Evidence location references and excerpts;
- arbitrary Research, Source and Observation metadata;
- raw Artifact contents;
- provider-private payloads.

The index retains only the metadata required for useful discovery and authorization, including
canonical Research identifiers, titles or bounded summaries, status/freshness, Research class,
Claim category/confidence, Evidence relation, owner scope, Project/Workspace/Task/Run references
and safe canonical relationship identifiers.

Exact source revision/hash/snapshot and Evidence details remain available through the authorized
Research Control Plane resource, not through the derived Search document.

## Authorization

Search results pass through two checks before counts or result bodies are returned:

1. the existing Control Plane/#15 authorization check for the registered collection;
2. the Research domain `search_result_allowed(...)` check, which resolves the canonical
   `ResearchItem` and requires an exact authenticated owner match.

This second check applies to Research Items and to Sources, Observations, Claims and Evidence by
following their canonical `research_item_id` ownership. A stale Search document whose canonical
record no longer resolves is denied rather than disclosed.

Search therefore cannot be used as an exact-ID or count side channel across Research owners.

## Authority boundaries

Search integration does not change Research semantics:

- Search does not create or mutate Research records;
- Search does not make a Claim supported or decision-ready;
- Search does not verify Evidence;
- Search does not grant permissions or Approval;
- Search does not execute source material;
- Search does not replace File/Artifact snapshot storage.

Canonical Research state, #86 Verification, #15 authorization, Files/Artifacts and the normal
Task/Plan/Run lifecycle remain authoritative.
