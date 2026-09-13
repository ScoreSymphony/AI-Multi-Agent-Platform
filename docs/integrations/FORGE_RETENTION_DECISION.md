# Forge retention decision

Status date: 2026-09-13

Issue: #991 — Executor: Forge retention decision, removal plan, and generic-guarantee audit

Architecture authority: [`ADR 0013`](../adr/0013-deprecate-forge-execution-adapter-pending-removal-gates.md). This document is the detailed inventory, evidence and removal-gate companion to that accepted decision.

Decision: **deprecate the active Forge execution backend; do not remove it yet.**

Forge is no longer treated as a preferred or reference execution backend. The current integration remains temporarily available as a compatibility path while the platform proves that all generic executor guarantees and the reference golden path remain independent of Forge. Removal is gated by the conditions below.

This decision does **not** promote Agent-Sandbox, SWE-ReX, OpenShell, or any other experimental backend. Their support state remains evidence-bound by #904.

## Why Forge is being deprecated

Issue #9 produced a genuine optional execution-only Forge boundary rather than importing Forge lifecycle ownership into the platform. The resulting integration is technically valid and useful as migration evidence: the platform owns canonical Task/Run state, identities, retry policy, events, recovery and workspace rules, while Forge is subordinate to the canonical `Executor` contract.

The evidence collected since then does not justify keeping Forge as a long-term `supported` backend:

- the real CI integration builds the pinned Rust execution sidecar and validates health, execute and cancellation behavior, but the proven sidecar profile exercises the `null` executor rather than a non-null Forge CLI executor family;
- no current supported product profile depends on a Forge-specific execution capability that the reference path cannot replace;
- most of the valuable behavior associated with the old Forge implementation is now owned by platform contracts and kernel/runtime code rather than by Forge itself;
- retaining Forge adds a separate Rust sidecar, a cross-repository pinned source revision, an additional network/security boundary, dedicated CI, provenance tracking and compatibility maintenance;
- newer executor candidates do not yet have enough evidence to replace Forge as a supported backend, so the correct transition is Forge `deprecated` plus the platform-owned reference executor, not a backend swap.

The remaining Forge integration is therefore a compatibility and regression bridge, not a strategic execution backend.

## Current Forge-specific inventory

The active removal surface includes at least:

| Area | Forge-specific surface | Removal treatment |
| --- | --- | --- |
| Adapter | `src/ai_multi_agent_platform/adapters/forge.py` | Remove after the gates below pass. |
| Transport | `src/ai_multi_agent_platform/adapters/forge_http.py` | Remove with the Forge adapter/sidecar profile. |
| Contract tests | `tests/contract/execution/test_forge_executor.py` | Preserve generic assertions in shared/reference contract coverage before removal. |
| Optionality tests | `tests/contract/forge/` | Remove only after no core import/config path references Forge. |
| Integration tests | `tests/integration/forge/` | Remove with the sidecar integration profile. |
| Regression tests | `tests/regression/forge/` | Move any still-unique kernel/recovery guarantee into backend-neutral regression coverage before removal. |
| CI | `forge-sidecar-integration` workflow path/job | Remove only after Forge is no longer a supported/deprecated executable profile. |
| Support matrix | `docs/ADAPTER_SUPPORT_MATRIX.toml` and generated/derived documentation | Mark Forge deprecated now; remove the active row when code is removed. |
| Adapter documentation | `docs/integrations/FORGE_ADAPTER.md` | Retain during deprecation with an explicit status banner; archive/remove from active docs with code removal. |
| Audit/provenance | `docs/integrations/FORGE_REUSE_AUDIT.md`, transport/reuse assessments, `upstream/forge-ai-agent-vps.yaml` | Preserve as historical architecture/provenance evidence unless it becomes misleading. |
| External sidecar source | pinned `ScoreSymphony/AI-Agent-VPS` Forge sidecar revision | Stop consuming it when the integration/CI path is removed; historical provenance may remain. |

Before final deletion, re-run repository search and enumerate any new Forge-specific config, factory wiring, runtime branches, docs, tests or workflow references introduced after this decision.

## Generic guarantee preservation matrix

Removing Forge must not remove any platform guarantee that happened to be tested through the Forge adapter.

| Guarantee | Canonical owner after Forge removal | Required preservation evidence |
| --- | --- | --- |
| `task_id`, `run_id`, `step_id`, correlation and dispatch identity | Platform execution/lifecycle contracts and kernel | Shared/reference contract and golden-path tests preserve canonical identity without backend-private IDs becoming canonical. |
| Provider/broker/attempt identity | Platform lifecycle/execution metadata | Backend identity remains namespaced metadata and survives the required lifecycle/replay paths. |
| Artifact and log provenance | Platform execution result, artifact/evidence and event model | Reference/backend-neutral tests reject invalid evidence and retain origin/identity metadata. |
| Cancellation and cancellation acknowledgement | Canonical `Executor`/lifecycle contract | Reference/backend-neutral tests cover pre-cancel, in-flight cancel and acknowledgement semantics where applicable. |
| Timeout semantics | Canonical executor/lifecycle contract | Reference/backend-neutral tests cover timeout classification and cleanup/cancellation ownership. |
| Retry ownership | Platform kernel/orchestrator, not executor adapter | Regression tests prove adapters do not create an independent retry state machine. |
| Worker/process recovery | Platform kernel, lifecycle backend and worker/runtime layer | Backend-neutral restart/reconciliation coverage preserves no-blind-redispatch behavior. |
| Event sequencing and replay | Platform event/kernel persistence | Existing kernel tests remain authoritative; any Forge-only regression assertion is generalized before deletion. |
| Workspace/root isolation | Platform workspace rules plus executor boundary | Reference/backend-neutral confinement tests cover missing roots, traversal and execution-root enforcement. |
| Path-boundary security | Platform security/workspace policy | Backend-neutral security tests cover artifact/evidence escape rejection and canonical path validation. |
| Remote execution | Node/worker/runtime architecture | Forge is not retained merely as a nominal remote-execution owner; any required remote profile must be proven independently behind the canonical executor/runtime boundary. |

## Backend comparison

| Backend | Current role | Evidence relevant to #991 | Decision here |
| --- | --- | --- | --- |
| `ReferenceExecutor` | Platform-owned baseline/reference executor | Local, deterministic reference/conformance path without Forge sidecar dependency | Remains the baseline and migration target. |
| `ForgeExecutor` | Optional Forge sidecar compatibility path | Contract coverage plus real pinned sidecar CI; proven real profile currently does not establish a non-null Forge CLI capability advantage | **Deprecated pending removal gates.** |
| Agent-Sandbox | Candidate higher-isolation executor | Evaluation exists, but promotion remains live-evidence-gated under #904/#798 | No promotion. |
| SWE-ReX | Experimental executor candidate | Explicitly experimental/evaluation-only evidence under #861/#904 | No promotion. |
| OpenShell / other candidates | Experimental/evaluation candidates | Evidence remains governed by #904 | No promotion. |

A replacement backend is not required in order to remove Forge. The reference executor plus the platform's generic runtime/worker boundaries are sufficient as the architectural baseline; specialized backends must earn their own support tier independently.

## Removal gates

Forge code and its sidecar CI profile may be removed when all of the following are true:

1. the #889 reference golden path passes without loading, configuring or invoking Forge-specific code;
2. the relevant #46 executor/lifecycle/conformance scenarios pass without Forge-specific dependencies;
3. no supported deployment, fixture or documented default profile depends on Forge;
4. every generic guarantee in the table above has backend-neutral or reference-path evidence outside Forge-specific test suites;
5. any still-useful Forge regression assertions have been moved to shared/reference kernel, lifecycle, workspace or execution tests;
6. the support matrix and active architecture/config documentation no longer present Forge as required or preferred;
7. repository search confirms that removing the adapter, transport, CI lane and executable profile does not leave stale active-path references.

The removal patch should then delete the active adapter/transport, Forge-only executable tests/configuration and sidecar CI integration in one bounded change while retaining historical provenance/audit material that still explains architectural decisions.

## Reversal / keep criteria

Before final removal, Forge may be reconsidered only if a non-null Forge execution profile obtains concrete evidence meeting the #904 support criteria **and** demonstrates a capability or maturity advantage worth the additional Rust, cross-repository, CI and security maintenance surface.

Passing contract-shape tests or the existing `null` sidecar integration alone is not sufficient for re-promotion.

If such evidence appears, open a separate promotion/retention decision rather than silently restoring `supported` status.

## Consequences

- Forge is treated as a deprecated compatibility backend, not a privileged platform component.
- No new platform feature should depend specifically on Forge.
- New generic execution guarantees must be tested through shared contracts/reference paths first.
- Forge-specific fixes during the deprecation window should be limited to correctness, security and removal-enabling work unless a separate retention decision changes direction.
- Historical audit/provenance material remains useful even after executable Forge code is removed.
