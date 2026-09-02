# Upstream Component Registry

This document is the canonical inventory for third-party components used by the AI Multi-Agent Platform.

No upstream component becomes part of the platform baseline merely because it is mentioned in architecture discussions or roadmap issues. A component is considered **integrated** only after its provenance entry is completed and the corresponding implementation is merged.

## Required fields

Each integrated component must record:

- **Name**
- **Purpose**
- **Integration category**: vendored/forked source, library dependency, external self-hosted service, protocol/specification, or optional adapter
- **Canonical upstream**
- **Pinned version/tag/commit**
- **Verified license**
- **License verification date**
- **Local path or interface**
- **Modified locally**: yes/no
- **Required for baseline**: yes/no
- **Recurring paid service required**: yes/no
- **Update method**
- **Notes / retained notices**

## Current integrated upstreams

None.

The repository currently contains only project-owned baseline code and documentation. Candidate integrations such as orchestrators, execution backends, model gateways and tool protocols must be added here only when their implementation work begins and their current license/provenance has been verified.

## Entry template

```text
### <Component name>
- Purpose:
- Integration category:
- Canonical upstream:
- Pinned version/tag/commit:
- Verified license:
- License verification date:
- Local path or interface:
- Modified locally: yes/no
- Required for baseline: yes/no
- Recurring paid service required: yes/no
- Update method:
- Notes / retained notices:
```

## Status semantics

- **Candidate**: under consideration; not an integrated dependency.
- **Approved**: provenance and compatibility reviewed; implementation may proceed.
- **Integrated**: implementation merged and inventory complete.
- **Deprecated**: still present but scheduled for replacement/removal.
- **Removed**: no longer used by the platform.

Candidate components should normally be discussed in their implementation issue or an ADR rather than being listed as integrated here.
