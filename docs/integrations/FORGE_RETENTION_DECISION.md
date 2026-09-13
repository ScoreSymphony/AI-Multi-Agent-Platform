# Forge retention decision

Status date: 2026-09-13

Issue: #991 — Executor: Forge retention decision, removal plan, and generic-guarantee audit

Architecture authority: [`ADR 0013`](../adr/0013-deprecate-forge-execution-adapter-pending-removal-gates.md). That ADR records the deprecation decision and removal gates; this document records their completion and the final retirement outcome.

Decision: **remove Forge as a first-party executable execution backend.**

Forge is no longer a supported, deprecated, experimental, or otherwise selectable first-party Executor profile. The platform-owned `Executor` contract and `ReferenceExecutor` remain the baseline. Agent-Sandbox, OpenShell and SWE-ReX remain independently classified experimental backends; removing Forge does not promote any of them.

## Why removal is justified

Issue #9 established a genuine optional execution-only Forge boundary rather than importing Forge Task/Project lifecycle ownership into the platform. That work remains useful architecture evidence, but the integration did not justify ongoing runtime maintenance:

- the pinned Rust sidecar CI proved the transport/runtime boundary with its deterministic `null` executor profile rather than a non-null Forge CLI capability advantage;
- no supported product profile depended on a Forge-only capability;
- canonical Task/Run identity, lifecycle, retry ownership, recovery, events, workspace rules and provenance are platform-owned guarantees;
- retaining Forge required a separate Rust sidecar, cross-repository pin, network/security boundary, dedicated CI lane and compatibility maintenance;
- #889 and #46 subsequently proved the maintained reference/generic paths independently of Forge.

The correct consolidation is therefore removal, not replacement by another external backend.

## Removal-gate evidence

All documented gates were satisfied before executable retirement:

1. **#889 golden path:** the maintained reference multi-agent path is explicitly Hermes/Forge-free and preserves canonical Plan/Step/Run/AgentRun, handoff, context, result and verification provenance.
2. **#46 conformance:** the reference/local vertical, failure/retry, restart/recovery, distributed identity/reconciliation and architecture-invariant coverage are backend-neutral.
3. **No deployment dependency:** no supported baseline deployment requires Forge.
4. **Generic guarantees moved outside Forge suites:** executor contracts, kernel/runtime tests, workspace/security coverage and distributed/recovery coverage own the guarantees below.
5. **No Forge-only regression requirement remains:** the former Forge regression suite was removed only after useful generic assertions existed elsewhere.
6. **Support/architecture status:** Forge was first demoted to `deprecated`; after the gates passed, the active support/release claims were removed.
7. **Cleanup:** adapter/transport, Forge-only executable tests, sidecar CI and maintained optional conformance evidence were removed together; provenance/audit records remain explicitly historical.

## Generic guarantee preservation matrix

| Guarantee | Canonical owner after Forge removal | Preservation evidence |
| --- | --- | --- |
| `task_id`, `run_id`, `step_id`, correlation and dispatch identity | Platform execution/lifecycle contracts and kernel | Shared/reference contract and golden-path coverage preserve canonical identity without backend-private IDs becoming canonical. |
| Provider/broker/attempt identity | Platform lifecycle/execution metadata | Backend identity remains namespaced metadata and survives canonical lifecycle/replay where applicable. |
| Artifact and log provenance | Platform execution result, artifact/evidence and event model | Reference/backend-neutral tests reject invalid evidence and retain origin/identity metadata. |
| Cancellation and acknowledgement | Canonical `Executor`/lifecycle contract | Reference/backend-neutral coverage includes pre-cancel and in-flight cancellation semantics. |
| Timeout semantics | Canonical executor/lifecycle contract | Shared/reference coverage owns timeout classification and cleanup/cancellation responsibility. |
| Retry ownership | Platform kernel/orchestrator | Regression coverage prevents executors from owning an independent retry state machine. |
| Worker/process recovery | Platform kernel, lifecycle backend and worker/runtime layer | Restart/reconciliation coverage preserves no-blind-redispatch behavior. |
| Event sequencing and replay | Platform event/kernel persistence | Kernel persistence/replay tests remain authoritative. |
| Workspace/root isolation | Platform workspace rules plus executor boundary | Reference/backend-neutral confinement covers missing roots, traversal and execution-root enforcement. |
| Path-boundary security | Platform security/workspace policy | Backend-neutral artifact/evidence and canonical-path validation remains authoritative. |
| Remote execution | Node/Worker/runtime architecture | Distributed Worker coverage proves remote execution independently of Forge. |

Concrete evidence includes `tests/contract/execution/executor_contract_suite.py`, `tests/contract/execution/test_reference_executor.py`, `tests/integration/kernel/test_executor_kernel_integration.py`, the #889 reference multi-agent golden path and the #46 distributed/recovery/security scenarios.

## Executable surface removed

The #991 retirement removes the active runtime surface as one bounded change:

- `src/ai_multi_agent_platform/adapters/forge.py`;
- `src/ai_multi_agent_platform/adapters/forge_http.py`;
- Forge-only executor/optionality/HTTP/sidecar tests;
- the active Forge adapter documentation;
- the `forge-sidecar-integration` CI job and cross-repository sidecar checkout/build;
- the maintained #46 Forge external evidence command;
- `executor.forge` from the first-party support matrix;
- Forge from current release compatibility metadata.

The former #46 Scenario C identifier may remain only as a non-executable historical compatibility slot; it has no maintained acceptance command and cannot establish a current Forge claim.

## Historical material retained intentionally

The following kinds of material may continue to mention Forge because they explain provenance, rejected architecture choices or historical compatibility behavior rather than exposing an executable backend:

- ADR 0013;
- `FORGE_REUSE_AUDIT.md`, `FORGE_REUSE_STATUS.md` and transport assessment records;
- `upstream/forge-ai-agent-vps.yaml`, now marked `removed` and historical;
- generic portability validation that rejects historical backend-private fields such as old Forge job IDs;
- historical validation/release notes that accurately describe earlier CI runs.

These references must not be interpreted as an active support, deployment or compatibility claim.

## Migration / state consequences

No canonical-state migration is required. Forge IDs were never canonical Task/Run/Step identities, and Forge did not own platform lifecycle, authorization, retry or event truth. Existing supported deployments use `ReferenceExecutor` or another independently supported/runtime-approved Executor implementation.

A future Forge reintroduction is not a rollback of this decision. It requires a new adoption/support decision with current provenance, security review, demonstrated capability value and fresh contract/integration/conformance evidence.
