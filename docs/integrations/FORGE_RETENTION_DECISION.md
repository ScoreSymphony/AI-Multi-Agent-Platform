# Forge retention and removal decision

Status date: 2026-09-13

Issue: #991 — Executor: Forge retention decision, removal plan, and generic-guarantee audit

Architecture authority: [`ADR 0013`](../adr/0013-deprecate-forge-execution-adapter-pending-removal-gates.md) recorded the bounded deprecation phase; [`ADR 0014`](../adr/0014-remove-forge-execution-adapter-after-removal-gates.md) records the final executable removal after those gates were satisfied.

Final decision: **remove Forge as an active first-party execution backend.**

Forge is no longer shipped as an executable adapter, transport, CI profile, support-matrix entry, release-compatibility component, or maintained #46 compatibility path. Historical audit and provenance records remain where they explain the architecture and reuse history.

This decision does **not** promote Agent-Sandbox, SWE-ReX, OpenShell, or any other experimental backend. Their support state remains evidence-bound by #904.

## Why Forge was removed

Issue #9 produced a genuine optional execution-only Forge boundary rather than importing Forge lifecycle ownership into the platform. The integration was technically valid and useful as migration evidence: the platform owned canonical Task/Run state, identities, retry policy, events, recovery and workspace rules, while Forge remained subordinate to the canonical `Executor` contract.

Long-term retention was not justified:

- the real CI integration built the pinned Rust execution sidecar and validated health, execute and cancellation behavior, but the maintained profile exercised the `null` executor rather than a non-null Forge CLI executor family;
- no supported product profile depended on a Forge-specific capability unavailable through the reference/generic runtime architecture;
- generic behavior once associated with the old Forge implementation had moved into platform contracts, kernel/runtime code and backend-neutral tests;
- retaining Forge imposed a separate Rust sidecar, cross-repository pin, network/security boundary, dedicated CI lane, provenance/update work and compatibility maintenance;
- newer executor candidates do not yet have evidence sufficient for promotion, so removal is not a backend swap.

ADR 0013 therefore demoted Forge to a temporary `deprecated` compatibility bridge. Once the #889/#46 and generic-guarantee gates were satisfied, #991 completed the intended retirement.

## Removed active surface

| Area | Former Forge-specific surface | Final treatment |
| --- | --- | --- |
| Adapter | `src/ai_multi_agent_platform/adapters/forge.py` | Removed. |
| Transport | `src/ai_multi_agent_platform/adapters/forge_http.py` | Removed. |
| Contract tests | `tests/contract/execution/test_forge_executor.py` | Removed after generic assertions were covered by shared/reference tests. |
| Optionality tests | `tests/contract/forge/` | Removed after active import/config paths were eliminated. |
| Integration tests | `tests/integration/forge/` | Removed with the sidecar profile. |
| Regression tests | former `tests/regression/forge/` | Removed after useful lifecycle/recovery assertions were generalized. |
| CI | `forge-sidecar-integration` | Removed from `.github/workflows/ci.yml`. |
| #46 evidence | executable Scenario C external profile | Removed; stable Scenario C remains only as a fail-closed historical tombstone. |
| Support matrix | `executor.forge` | Removed from the active first-party inventory. |
| Release compatibility | Forge component in both compatibility inventories | Removed. |
| Adapter documentation | `docs/integrations/FORGE_ADAPTER.md` | Removed from active documentation; history remains in ADR/audit/provenance records. |
| Audit/provenance | reuse audits and `upstream/forge-ai-agent-vps.yaml` | Retained as historical evidence and marked non-active/removed. |
| External sidecar source | pinned `ScoreSymphony/AI-Agent-VPS` revision | No longer consumed by CI/runtime; pin retained only for provenance. |

## Generic guarantee preservation matrix

Removing Forge does not remove any platform guarantee that happened to be tested through the adapter.

| Guarantee | Canonical owner after Forge removal | Preservation evidence |
| --- | --- | --- |
| `task_id`, `run_id`, `step_id`, correlation and dispatch identity | Platform execution/lifecycle contracts and kernel | Shared/reference contract, kernel integration and #889/#46 golden-path coverage. |
| Provider/broker/attempt identity | Platform lifecycle/execution metadata | Backend identity remains namespaced metadata and survives canonical persistence/replay. |
| Artifact and log provenance | Platform execution result, artifact/evidence and event model | Reference/backend-neutral artifact-boundary and lifecycle evidence. |
| Cancellation and cancellation acknowledgement | Canonical `Executor`/lifecycle contract | Shared pre-cancel coverage plus explicit `ReferenceExecutor` in-flight cancellation acknowledgement. |
| Timeout semantics | Canonical executor/lifecycle contract | Shared/reference timeout classification coverage. |
| Retry ownership | Platform kernel/orchestrator | Failure/retry conformance proves canonical retry ownership rather than adapter-owned retries. |
| Worker/process recovery | Kernel, lifecycle backend and distributed runtime | Backend-neutral restart/reconciliation and lost-owner/fencing coverage prevents blind redispatch. |
| Event sequencing and replay | Platform event/kernel persistence | Kernel persistence/replay tests remain authoritative. |
| Workspace/root isolation | Platform workspace rules plus executor boundary | Reference/backend-neutral missing-root, traversal and confinement coverage. |
| Path-boundary security | Platform security/workspace policy | Backend-neutral artifact/evidence escape and canonical path validation coverage. |
| Remote execution | Node/Worker/runtime architecture | Distributed runtime/conformance proves remote execution independently of Forge. |

## Removal-gate evidence

The removal gates are satisfied as follows:

1. **#889 golden path** — completed; the maintained reference multi-agent path explicitly runs without Hermes/Forge fallback ownership.
2. **#46 conformance** — completed; reference/local, failure/retry, restart/recovery, distributed identity/reconciliation and architecture-invariant evidence exists independently of Forge.
3. **No supported deployment dependency** — the reference baseline and supported deployment surfaces do not require Forge.
4. **Generic guarantees outside Forge** — `tests/contract/execution/executor_contract_suite.py`, `tests/contract/execution/test_reference_executor.py`, kernel/lifecycle integration, security/confinement and distributed recovery coverage own the relevant semantics.
5. **Forge-only regression assertions generalized** — canonical identity, namespaced metadata, SQLite replay/idempotency and no-blind-redispatch behavior are maintained outside Forge-specific suites.
6. **Support/config architecture cleanup** — the active support matrix and release compatibility inventory contain no Forge implementation claim; no experimental replacement was promoted.
7. **Executable cleanup** — adapter, transport, Forge-only tests, external Scenario C evidence and sidecar CI consumption are removed. Historical references that remain are explicitly non-active provenance or architecture examples.

The executable removal patch must still pass normal repository CI before merge; the gate decision above describes the intended repository state of this patch, not permission to bypass CI.

## Backend comparison after removal

| Backend | Current role | #991 outcome |
| --- | --- | --- |
| `ReferenceExecutor` | Platform-owned baseline/reference executor | Remains the baseline and migration target. |
| Forge | Historical removed first-party adapter | No active support or compatibility claim. |
| Agent-Sandbox | Candidate higher-isolation executor | Remains experimental/evidence-gated under #904/#798. |
| SWE-ReX | Experimental executor candidate | Remains experimental/evaluation-only under #861/#904. |
| OpenShell / other candidates | Experimental/evaluation candidates | Remain governed independently by #904. |

A replacement backend was not required to remove Forge. The reference executor plus the generic Worker/runtime architecture are sufficient as the architectural baseline; specialized backends must earn their own support tier independently.

## Historical material retained intentionally

The following kinds of material may continue to mention Forge because they explain provenance, rejected architecture choices or historical compatibility behavior rather than exposing an executable backend:

- ADR 0013 and ADR 0014;
- `FORGE_REUSE_AUDIT.md`, `FORGE_REUSE_STATUS.md` and transport assessment records;
- `upstream/forge-ai-agent-vps.yaml`, now marked `removed` and historical;
- generic portability validation that rejects historical backend-private fields such as old Forge job IDs;
- historical validation/release notes that accurately describe earlier CI runs.

These references must not be interpreted as an active support, deployment or compatibility claim.

## Re-promotion rule

Forge can return only through a new explicit architecture/support decision. Such a proposal must provide non-null execution evidence meeting #904 and demonstrate a capability or maturity advantage sufficient to justify the Rust, cross-repository, CI and security maintenance surface.

The historical `null` sidecar proof is insufficient for re-promotion. Reintroduction must not silently restore old support metadata or executable paths.

## Migration / state consequences

No canonical-state migration is required. Forge IDs were never canonical Task/Run/Step identities, and Forge did not own platform lifecycle, authorization, retry or event truth. Existing supported deployments use `ReferenceExecutor` or another independently supported/runtime-approved Executor implementation.

## Consequences

- Forge is no longer an active platform backend.
- The canonical `Executor` contract and generic execution guarantees remain unchanged.
- `ReferenceExecutor` remains the reference baseline.
- Experimental alternatives keep their prior evidence-bound tiers.
- Stable conformance identifier C is historical/fail-closed rather than executable compatibility evidence.
- Historical audit/provenance material remains useful, but must not read as an active runtime or support claim.
- Future generic execution guarantees continue to be proven through shared/reference paths first.
