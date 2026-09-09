# ADR 0011 — Repository-intelligence v1 core and optional enhanced providers

Status: **Accepted**

Date: 2026-09-09

Affected issues/contracts: #502, #12 Capability Registry, #15 Authorization, #20 Plugins, #37 Workspaces, #45 Search, #81 Registry/Marketplace, #82 Repository integration, #19 Evaluation, #14/#500 resource admission.

## Context

Issue #502 originally mixed two different concerns:

1. the platform-level repository/code-intelligence capability required by coding, planning, review
   and research workflows; and
2. optional enhanced third-party indexers/providers such as ProjectAtlas, Graphify, CodeGraph and
   Understand Anything.

The issue owner later clarified that repository/code intelligence itself is part of the operational
v1 core, while the choice and adoption of named enhanced providers remains optional. The
point-in-time `IMPLEMENTATION_ROADMAP.md` snapshot predates the completed consolidation work and
therefore still describes #502 as a ready optional expansion lane with work that is now present on
`main` or deliberately outside the clarified v1 closure boundary.

The repository now contains the provider-neutral capability taxonomy, deterministic local baseline,
Repository/Workspace/Authorization production wiring, exact revision and dirty-Workspace
provenance, deterministic fallback, bounded context-funnel and scoped Search integration, resource
contracts, candidate plugin lifecycle support and safe optional Registry representation.

ProjectAtlas v0.4.5 has additionally completed a pinned, checksum-verified, read-only,
provider-state-isolated and no-network evaluation pilot. The final #502 acceptance workflow measures
it against a deterministic Git reference path on the same immutable fixture revision. The pilot does
not establish representative graph/symbol value, large-repository resource behavior, dirty-Workspace
provider freshness or a deployment-owned production sandbox.

## Decision

The canonical v1 repository-intelligence architecture is the **provider-neutral platform layer plus
the deterministic local baseline**.

Named enhanced providers are optional extensions. No ProjectAtlas-, Graphify-, CodeGraph- or
Understand-Anything-specific type, lifecycle or state becomes canonical platform architecture.

For #502 specifically:

- the provider-neutral v1 core is considered complete once its closing PR passes exact-head required
  CI/security checks and the dedicated repository-intelligence comparison workflow;
- ProjectAtlas v0.4.5 remains `experimental/deferred`;
- ProjectAtlas is not the default provider;
- ProjectAtlas production repository map/search/source-slice/symbol/graph capabilities remain
  disabled;
- the CI seccomp wrapper is evidence for the evaluation boundary only and is not a generic
  production worker sandbox;
- unmeasured provider benefits or costs must remain explicit rather than being inferred from the
  tiny fixture;
- future adoption of ProjectAtlas or another enhanced provider is follow-up work and must not keep
  the completed provider-neutral v1 core issue open indefinitely.

This ADR supersedes the **execution-status claims for #502** in the 2026-09-07 point-in-time
`IMPLEMENTATION_ROADMAP.md` snapshot. It does not change that roadmap's dependency model for other
issues. Current GitHub issue state and wording remain the live execution source of truth as already
stated by the roadmap and `AGENTS.md`.

## Consequences

- Single-node and other supported profiles remain usable without any third-party repository indexer.
- Optional providers continue to integrate through #12/#20 and canonical Repository/Workspace/
  security boundaries rather than becoming a second repository or workspace authority.
- Derived provider indexes remain non-canonical and rebuildable.
- Registry listing does not imply installation, activation or trust.
- A future provider-adoption issue must supply the evidence appropriate to the capability it wants
  to activate, including strict version-pinned output normalization where required, representative
  correctness/resource/freshness measurements and a production-owned execution boundary.
- The historical #502 integration handoff and old roadmap snapshot remain useful provenance but must
  not be read as current issue status after this ADR and the final completion record.

## Alternatives considered

### Keep #502 open until ProjectAtlas is production-ready

Rejected. It would turn an optional provider into a hidden v1 dependency and contradict the issue
owner's scope correction. It would also make the provider-neutral baseline's completion contingent
on one named upstream.

### Adopt ProjectAtlas as the default from the tiny pilot

Rejected. The current comparison establishes real contained execution and basic timing/state cost,
but not the differentiated correctness, freshness and representative operating-envelope evidence
needed to justify default adoption.

### Remove ProjectAtlas integration work entirely

Rejected. The candidate shell, containment harness, evaluation evidence and provider-neutral seams
are useful reversible assets and demonstrate that the extension architecture works without making
the candidate canonical.
