# Forge execution adapter (removed)

Status: **Removed from the active codebase under #991 after the documented removal gates passed.**

Forge was originally integrated under #9 as an optional execution-only backend behind the platform-owned `Executor` contract. It was later demoted to a bounded `deprecated` compatibility backend by ADR 0013 because the maintained real sidecar evidence proved the transport/runtime boundary through the sidecar's deterministic `null` executor, but did not demonstrate a non-null Forge CLI capability advantage sufficient to justify the separate Rust sidecar, cross-repository pin, CI lane and security/transport maintenance surface.

After #889 and #46 completed and the remaining generic executor guarantees were proven through shared/reference coverage, #991 retired the executable integration. The repository no longer ships:

- `ForgeExecutor`;
- `ForgeHttpClient` or the `forge-executor-sidecar/v1` transport path;
- Forge-only contract/integration tests;
- the Forge sidecar CI lane;
- an active Forge support-matrix or release-compatibility claim;
- executable #46 Scenario C evidence.

The stable Scenario C identifier remains only as a fail-closed historical conformance tombstone. It has no executable evidence and must not be interpreted as an available Forge profile.

## Guarantees preserved after removal

Forge removal did not change ownership of platform execution semantics. The following guarantees remain platform-owned and are exercised outside Forge-specific code:

- canonical Task/Run/Step/correlation and dispatch identity;
- namespaced provider/backend metadata rather than backend IDs becoming canonical;
- artifact and log/evidence provenance;
- pre-dispatch and in-flight cancellation semantics;
- timeout classification;
- platform-owned retry policy;
- restart/reconciliation without blind redispatch;
- ordered/replayable canonical event history;
- workspace/root confinement and path-boundary security;
- remote/distributed execution through the generic Node/Worker/runtime architecture.

`ReferenceExecutor` remains the reference baseline. Agent-Sandbox, OpenShell, SWE-ReX and other candidates retain their independently evidenced support tiers; Forge removal does not promote any of them.

## Historical implementation shape

For architecture history only, the removed integration had two layers:

- `ForgeExecutor` implemented the canonical `Executor` interface;
- a platform-owned `ForgeClient` protocol was implemented by `ForgeHttpClient` against the pinned Rust sidecar.

Canonical Task/Run/Step/correlation IDs always remained platform-owned. Forge execution identifiers were adapter-private metadata, and Forge never became canonical lifecycle, retry, event or recovery authority.

The generic lifecycle/recovery assertions that had once lived in Forge-specific regression tests were migrated to backend-neutral `ExecutorLifecycleBackend`, kernel/recovery, reference-executor and #46 conformance coverage before executable removal.

## Historical provenance

The original behavior/reuse audit remains in [`FORGE_REUSE_AUDIT.md`](FORGE_REUSE_AUDIT.md). The detailed retention/removal evidence and gate closure are recorded in [`FORGE_RETENTION_DECISION.md`](FORGE_RETENTION_DECISION.md). `upstream/forge-ai-agent-vps.yaml` is retained as historical provenance for the pinned source that informed and supplied the removed sidecar integration.

No source from `ScoreSymphony/AI-Agent-VPS` was copied into the platform adapter. The historical reuse mode was adapter integration plus behavioral influence; after #991, only provenance and architecture history remain.
