# ADR 0013 — Deprecate Forge execution adapter pending removal gates

- **Status:** Superseded by ADR 0014
- **Issue:** #991
- **Date:** 2026-09-13

> Historical decision: this ADR governed the bounded Forge deprecation window. ADR 0014 records the final executable removal after the gates below were satisfied.

## Context

Issue #9 established a real, optional Forge execution boundary rather than importing Forge Task/Project lifecycle ownership into the platform. `ForgeExecutor` implemented the canonical `Executor` contract and the pinned Rust `forge-executor-sidecar/v1` was exercised in CI. The platform, not Forge, owned canonical Task/Run/Step identities, lifecycle state, retry policy, event history, reconciliation and workspace/path rules.

That integration was valid compatibility evidence, but the proven real sidecar profile used the deterministic `null` executor. It therefore proved the transport/runtime boundary without proving a non-null Forge CLI execution capability that justified keeping Forge as a first-party `supported` backend. Retaining Forge also carried a Rust sidecar build, a cross-repository pin, a dedicated CI lane and an additional transport/security maintenance surface.

At the same time, Agent-Sandbox, SWE-ReX, OpenShell and other candidate backends remained evidence-gated under #904. Deprecating Forge could not be used to promote any of them beyond their independently proven support tier.

The detailed inventory, capability-preservation matrix and removal checklist are maintained in `docs/integrations/FORGE_RETENTION_DECISION.md`.

## Decision

Forge was **deprecated as a first-party execution backend**.

During this ADR's active window, the executable Forge adapter, HTTP transport and pinned sidecar compatibility lane remained temporarily present only as a bounded compatibility/regression bridge while removal gates were completed. Forge was no longer a preferred, reference or strategic backend, and no new platform feature could depend specifically on it.

The platform-owned `ReferenceExecutor` remained the baseline execution implementation. This decision did not designate or promote a replacement specialized backend.

### Canonical guarantees remained platform-owned

Forge deprecation or eventual removal could not change ownership of these guarantees:

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

Where a guarantee was historically demonstrated only through Forge-specific tests, equivalent backend-neutral or reference-path evidence had to exist before the Forge-specific proof was removed.

### Removal gates

Executable Forge code and the sidecar CI lane could be removed only when all of the following were true:

1. the #889 reference multi-agent golden path passed without Forge-specific loading or configuration;
2. the relevant #46 executor/lifecycle/conformance coverage passed without Forge;
3. each generic guarantee listed above had backend-neutral or reference-path evidence outside Forge-specific suites;
4. no supported deployment/profile depended on Forge;
5. useful Forge-only regression assertions had been generalized before their original tests were deleted;
6. active support, release, provenance, architecture and configuration metadata no longer treated Forge as required or preferred;
7. repository cleanup proved no active package/import/config/CI dependency remained after executable removal.

Historical provenance and reuse/audit documentation could remain after executable removal when it still explained architectural history without presenting Forge as active support.

### Re-promotion gate

Forge could return to a supported tier only through a new explicit architecture/support decision backed by non-null execution evidence meeting #904 and demonstrating a concrete capability or maturity advantage sufficient to justify the extra Rust, cross-repository, CI and security maintenance surface. The historical `null` sidecar proof was insufficient.

## Consequences during the deprecation window

- `executor.forge` was represented as `deprecated` in the canonical adapter support matrix.
- Release compatibility metadata represented the Forge sidecar as `deprecated` so release validation emitted deprecation treatment.
- `upstream/forge-ai-agent-vps.yaml` and `docs/UPSTREAMS.md` represented the integration as deprecated and linked this ADR.
- The Forge adapter/transport and sidecar CI lane remained temporarily executable until the removal gates passed.
- New execution guarantees were proven first through shared contracts and the reference/backend-neutral path.
- Forge-specific maintenance was limited to correctness, security, compatibility and removal-enabling changes unless this ADR was superseded.
- Candidate replacement backends retained their evidence-bound tiers.

ADR 0014 supersedes these active-state consequences: the executable Forge integration and compatibility claim are now removed, while the historical reasoning remains valid.

## Alternatives considered

### Keep Forge `supported`

Rejected. The real sidecar boundary was valid, but the proven profile did not establish a non-null Forge CLI capability advantage sufficient to justify the ongoing maintenance surface.

### Remove Forge immediately

Rejected for the deprecation slice. #991 required generic guarantees, #889 golden-path coverage and relevant #46 conformance evidence to be independent of Forge before the executable compatibility path was deleted.

### Replace Forge immediately with Agent-Sandbox, SWE-ReX, OpenShell or another candidate

Rejected. #904 requires independent support evidence for each backend. Forge deprecation could not be used as evidence for another backend's promotion.

### Retain Forge only as historical source material and remove all executable integration immediately

Deferred by this ADR until the removal gates passed. ADR 0014 records completion of that intended end state.

## Affected issues and contracts

- #991 owns the Forge retention/removal decision and closure gates.
- #9 provides the completed Forge reuse and real-sidecar integration evidence.
- #904 owns adapter support tiers and prevents unsupported replacement promotion.
- #889 is the reference multi-agent golden-path validation consumer.
- #46 owns end-to-end platform conformance evidence relevant to executable removal.
- #798 and #861 provide comparison evidence for Agent-Sandbox and SWE-ReX respectively.
- `Executor`, `ExecutorLifecycleBackend`, canonical Task/Run/Step identity, event history, recovery and workspace/security contracts remain platform-owned and unchanged.
- `docs/integrations/FORGE_RETENTION_DECISION.md` is the detailed inventory/evidence companion to this decision history.
