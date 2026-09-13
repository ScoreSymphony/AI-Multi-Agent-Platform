# ADR 0013 — Deprecate Forge execution adapter pending removal gates

- **Status:** Accepted
- **Issue:** #991
- **Date:** 2026-09-13

## Context

Issue #9 established a real, optional Forge execution boundary rather than importing Forge Task/Project lifecycle ownership into the platform. `ForgeExecutor` implements the canonical `Executor` contract and the pinned Rust `forge-executor-sidecar/v1` is exercised in CI. The platform, not Forge, owns canonical Task/Run/Step identities, lifecycle state, retry policy, event history, reconciliation and workspace/path rules.

That integration remains valid compatibility evidence, but the currently proven real sidecar profile uses the deterministic `null` executor. It therefore proves the transport/runtime boundary without proving a non-null Forge CLI execution capability that justifies keeping Forge as a first-party `supported` backend. Retaining Forge also carries a Rust sidecar build, a cross-repository pin, a dedicated CI lane and an additional transport/security maintenance surface.

At the same time, Agent-Sandbox, SWE-ReX, OpenShell and other candidate backends remain evidence-gated under #904. Deprecating Forge must not be used to promote any of them beyond their independently proven support tier.

The detailed inventory, capability-preservation matrix and removal checklist are maintained in `docs/integrations/FORGE_RETENTION_DECISION.md`.

## Decision

Forge is **deprecated as a first-party execution backend**.

The executable Forge adapter, HTTP transport and pinned sidecar compatibility lane remain temporarily present only as a bounded compatibility/regression bridge while removal gates are completed. Forge is no longer a preferred, reference or strategic backend, and no new platform feature may depend specifically on it.

The platform-owned `ReferenceExecutor` remains the baseline execution implementation. This decision does not designate or promote a replacement specialized backend.

### Canonical guarantees remain platform-owned

Forge deprecation or eventual removal must not change ownership of these guarantees:

- canonical Task/Run/Step/correlation and dispatch identity;
- provider/broker/attempt identity as namespaced backend metadata rather than canonical identity;
- artifact and log provenance;
- cancellation and cancellation acknowledgement;
- timeout semantics;
- retry ownership;
- worker/process recovery and reconciliation without blind redispatch;
- event sequencing and replay;
- workspace/root isolation and path-boundary security;
- remote execution through the generic node/worker/runtime architecture when product scope requires it.

Where a guarantee was historically demonstrated only through Forge-specific tests, equivalent backend-neutral or reference-path evidence must exist before the Forge-specific proof is removed.

### Removal gates

Executable Forge code and the sidecar CI lane may be removed only when all of the following are true:

1. the #889 reference multi-agent golden path passes without Forge-specific loading or configuration;
2. the relevant #46 executor/lifecycle/conformance coverage passes without Forge;
3. each generic guarantee listed above has backend-neutral or reference-path evidence outside Forge-specific suites;
4. no supported deployment/profile depends on Forge;
5. useful Forge-only regression assertions have been generalized before their original tests are deleted;
6. active support, release, provenance, architecture and configuration metadata no longer treat Forge as required or preferred;
7. repository cleanup proves no active package/import/config/CI dependency remains after executable removal.

Historical provenance and reuse/audit documentation may remain after executable removal when it still explains architectural history without presenting Forge as active support.

### Re-promotion gate

Forge may return to a supported tier only through a new explicit architecture/support decision backed by non-null execution evidence that meets #904 and demonstrates a concrete capability or maturity advantage sufficient to justify the extra Rust, cross-repository, CI and security maintenance surface. The existing `null` sidecar proof is insufficient.

## Consequences

- `executor.forge` is represented as `deprecated` in the canonical adapter support matrix.
- Release compatibility metadata must represent the Forge sidecar as `deprecated`, so release validation emits deprecation warning/release-note treatment rather than reporting it as merely `tested`.
- `upstream/forge-ai-agent-vps.yaml` and `docs/UPSTREAMS.md` must represent the integration as deprecated and link this ADR.
- The Forge adapter/transport and sidecar CI lane remain temporarily executable until the removal gates pass.
- New execution guarantees are proven first through shared contracts and the reference/backend-neutral path.
- Forge-specific maintenance during the deprecation window is limited to correctness, security, compatibility and removal-enabling changes unless this ADR is superseded.
- Candidate replacement backends retain their existing evidence-bound tiers; this ADR does not promote them.

## Alternatives considered

### Keep Forge `supported`

Rejected. The real sidecar boundary is valid, but the proven profile does not establish a non-null Forge CLI capability advantage sufficient to justify the ongoing maintenance surface.

### Remove Forge immediately

Rejected for this slice. #991 requires generic guarantees, #889 golden-path coverage and relevant #46 conformance evidence to be independent of Forge before the executable compatibility path is deleted.

### Replace Forge immediately with Agent-Sandbox, SWE-ReX, OpenShell or another candidate

Rejected. #904 requires independent support evidence for each backend. Forge deprecation cannot be used as evidence for another backend's promotion.

### Retain Forge only as historical source material and remove all executable integration now

Deferred until the removal gates above pass. That is the intended end state if no re-promotion evidence emerges.

## Affected issues and contracts

- #991 owns the Forge retention/removal decision and closure gates.
- #9 provides the completed Forge reuse and real-sidecar integration evidence.
- #904 owns adapter support tiers and prevents unsupported replacement promotion.
- #889 is the reference multi-agent golden-path validation consumer.
- #46 owns end-to-end platform conformance evidence relevant to executable removal.
- #798 and #861 provide comparison evidence for Agent-Sandbox and SWE-ReX respectively.
- `Executor`, `ExecutorLifecycleBackend`, canonical Task/Run/Step identity, event history, recovery and workspace/security contracts remain platform-owned and unchanged.
- `docs/integrations/FORGE_RETENTION_DECISION.md` is the detailed inventory/evidence companion to this ADR.
