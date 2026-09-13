# ADR 0014 — Remove Forge execution adapter after removal gates

- **Status:** Accepted
- **Issue:** #991
- **Date:** 2026-09-13
- **Supersedes:** ADR 0013 for the active Forge support state

## Context

ADR 0013 demoted Forge from `supported` to a bounded `deprecated` compatibility backend. It deliberately retained the executable adapter, HTTP sidecar transport and CI lane until the platform proved that canonical execution guarantees, the #889 reference multi-agent golden path and the relevant #46 conformance/recovery paths were independent of Forge.

Those gates are now satisfied. #889 and #46 are complete, backend-neutral/reference coverage owns the generic identity, cancellation, timeout, artifact, retry, recovery, event, workspace and path-security semantics, and no supported deployment requires Forge. The maintained real Forge sidecar profile still exercises the deterministic `null` executor and does not establish a non-null Forge CLI capability advantage sufficient to justify its maintenance surface.

## Decision

Remove Forge as an active first-party execution backend.

The removal includes:

- `ForgeExecutor` and its platform-owned Forge HTTP transport;
- Forge-only contract, optionality and integration suites;
- the pinned Forge sidecar CI lane;
- executable #46 Scenario C evidence;
- the active `executor.forge` support-matrix row;
- Forge release-compatibility metadata.

Keep only historical provenance, reuse/audit material and a concise adapter tombstone where those records help explain architecture history. Stable conformance identifier C may remain as a non-executable fail-closed historical tombstone; it is not a compatibility claim.

`ReferenceExecutor` remains the reference baseline. Agent-Sandbox, OpenShell, SWE-ReX and other candidates retain their existing evidence-bound tiers. No replacement backend is promoted by this ADR.

## Preserved platform guarantees

Forge removal does not move or delete canonical authority for:

- Task/Run/Step/correlation and dispatch identity;
- namespaced provider/backend/attempt metadata;
- artifact and evidence provenance;
- cancellation and cancellation acknowledgement;
- timeout classification;
- retry ownership;
- worker/process recovery and reconciliation without blind redispatch;
- canonical event sequencing and replay;
- workspace/root confinement and path-boundary security;
- distributed/remote execution through the generic Node/Worker/runtime architecture.

These guarantees remain implemented and tested through platform-owned contracts, the reference executor, kernel/lifecycle coverage, security/confinement tests, distributed runtime tests and the maintained #889/#46 acceptance paths.

## Consequences

- Core package imports and normal runtime startup have no Forge executable dependency.
- CI no longer checks out or builds the historical `ScoreSymphony/AI-Agent-VPS` Forge sidecar source.
- Releases no longer publish a Forge compatibility component or support claim.
- The adapter support matrix contains no Forge first-party implementation entry.
- #46 Scenario C has no maintained executable evidence and fails closed if explicitly requested.
- Historical pinned source/provenance may remain documented, but must be marked removed/non-active.
- Reintroducing Forge requires a new explicit support/architecture decision with non-null execution evidence meeting #904; the old `null` sidecar proof is insufficient.

## Alternatives considered

### Keep Forge indefinitely as deprecated

Rejected. Once the removal gates are satisfied, retaining an executable but non-strategic sidecar only preserves maintenance, security and CI burden without a supported capability advantage.

### Promote another executor while removing Forge

Rejected. Removal evidence for Forge is not support evidence for another backend. Each candidate remains governed independently by #904.

### Delete all Forge references including provenance

Rejected. Historical reuse/audit records are useful for explaining why canonical lifecycle authority and execution boundaries evolved as they did, provided they are clearly marked historical and cannot be mistaken for active support.

## Evidence

The detailed inventory, guarantee-preservation matrix and gate closure are maintained in `docs/integrations/FORGE_RETENTION_DECISION.md`. ADR 0013 remains part of the decision history and documents why removal was intentionally deferred until these gates passed.
