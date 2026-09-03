# Forge reuse implementation status

Status date: 2026-09-03

Issue: #9 — Audit and port reusable Forge capabilities behind the execution interface

This document separates what is **implemented and proven** from what remains unresolved. It intentionally does not equate a tested Forge adapter boundary with a connected legacy Forge runtime.

## Merged implementation

PR #118 established the Phase 1–3 audit baseline:

- capability inventory;
- reuse/adapt/reimplement/reject/defer classification;
- canonical identity/lifecycle/event/workspace/error mappings;
- rejected legacy assumptions;
- source revision and MIT provenance.

PR #129 added the first code-bearing adapter/recovery slice:

- platform-owned `ForgeClient` protocol;
- optional `ForgeExecutor` behind canonical `Executor`;
- executor contract-suite coverage;
- canonical Task/Run/Step/correlation ID preservation;
- namespaced Forge execution metadata;
- timeout/cancellation/error/retry-hint translation;
- workspace and artifact path isolation;
- health translation;
- Forge optionality test;
- lifecycle bridge propagation of adapter metadata into canonical history;
- canonical Step-vs-Task identity correction in `ExecutorLifecycleBackend`;
- SQLite replay/idempotency/restart regressions with a Forge-backed execution boundary;
- missing-backend-job recovery that requires reconciliation instead of redispatch;
- explicit rejection of the legacy task-launch HTTP path as a concrete executor transport.

## Issue #9 acceptance criteria

| Acceptance criterion | Status | Evidence / remaining gap |
| --- | --- | --- |
| Reuse matrix with reuse/adapt/reimplement/reject/defer decisions | **Done** | `docs/FORGE_REUSE_AUDIT.md` |
| Architecture-significant decisions have rationale | **Done** | Audit plus `docs/FORGE_TRANSPORT_ASSESSMENT.md` |
| Copied/adapted code has provenance/license metadata | **Done for current implementation** | `upstream/forge-ai-agent-vps.yaml`; current adapter is new platform-owned code and no Forge source is copied. File-level copied-source provenance becomes mandatory if source is later ported. |
| Forge capabilities sit behind canonical execution/lifecycle adapter boundary | **Partially done** | `ForgeExecutor` and `ExecutorLifecycleBackend` are implemented and tested. A real Forge runtime implementation of `ForgeClient` is not yet connected. |
| Forge-private IDs/types do not become canonical contracts | **Done** | Forge execution ID is namespaced adapter metadata; Step/Task identity regression coverage exists. |
| Platform lifecycle ownership remains in new kernel | **Done** | Kernel owns Task/Run transitions, historical state and recovery; legacy task-launch transport was rejected specifically to prevent shadow lifecycle ownership. |
| Forge adapter passes executor contract tests where applicable | **Done for adapter boundary** | `tests/test_forge_executor.py` applies `ExecutorContractSuite`. A real-runtime conformance run remains dependent on selecting/extracting a runtime. |
| Recovery/idempotency/event behavior covered by regression tests | **Done for canonical reuse behavior** | `tests/test_forge_kernel_regressions.py` plus existing kernel persistence/recovery coverage. No second Forge event store was introduced. |
| Disabling Forge leaves core/reference execution functional | **Done** | `tests/test_forge_optionality.py` plus existing reference executor suite. |
| Rejected legacy assumptions are documented | **Done** | `docs/FORGE_REUSE_AUDIT.md` and transport assessment. |

## Current blocker: a genuine execution-only Forge runtime boundary

The old `AI-Agent-VPS` public API is not a suitable direct implementation of `ForgeClient.execute`.

Its manual execution start route is `POST /api/v1/tasks/{id}/launch`, and that route invokes Forge's own `TaskService` to create/start an execution for an existing Forge Task. Using it would therefore require mirrored Forge Task/Project state and would reintroduce a second lifecycle source of truth.

Useful legacy endpoints still exist for already-created backend executions (`healthz`, execution get/cancel/logs), but there is no clean public executor-only start operation matching the new platform `ExecutionRequest`.

## Runtime options considered

### A. Mirror platform Tasks into the legacy Forge HTTP API

**Decision: reject.**

Advantages:
- fastest path to calling the old application unchanged.

Problems:
- creates shadow Forge Tasks/Projects;
- introduces dual lifecycle/persistence semantics;
- makes platform execution depend on the full legacy product;
- complicates reconciliation and removal;
- directly conflicts with issue #9 non-goals.

### B. Add an executor-only endpoint to the full legacy AI-Agent-VPS application

**Decision: feasible but not preferred as the long-term platform architecture.**

The old API `AppState` already exposes `Arc<dyn TaskExecutor>`, so an endpoint could technically call the executor subsystem without `TaskService`.

However, making the new platform depend on that endpoint would still require deploying and maintaining the old application just to reach one subsystem. It would preserve a large legacy runtime dependency that the selective-port goal is intended to avoid.

This remains useful as a temporary compatibility bridge only if there is a concrete migration need.

### C. Selectively extract/port the Forge executor subsystem into an optional execution-only sidecar

**Decision: strongest candidate if actual Forge executor implementation reuse is required.**

The old `executors` crate is already separated around `TaskExecutor`, `ExecutionContext`, adapter routing, command execution, logs and cancellation. Its own manifest is substantially smaller than the full Forge application.

A sidecar/extracted runtime would:

- live behind the existing platform-owned `ForgeClient` protocol;
- expose execution-only start/status/cancel/health semantics;
- avoid Forge Task/Project lifecycle ownership;
- remain optional and replaceable;
- permit real reuse of proven Rust execution code;
- require explicit Rust build/deployment/provenance review rather than silently expanding the Python core.

Before implementation, verify the exact crate dependency closure and decide which executor families are still wanted. Do not copy the whole legacy workspace by default.

### D. Reimplement selected executor behavior natively behind `Executor`

**Decision: valid when source reuse is not worth the dependency cost.**

This preserves behavior/specification lessons without carrying the Rust subsystem. It is especially appropriate for workspace isolation, normalized errors, evidence and recovery mechanisms that already have platform-owned implementations.

It is less appropriate if the goal is specifically to retain mature legacy CLI executor adapters with minimal behavioral drift.

## Recommended next #9 slice

Do **not** connect the legacy task-launch API.

If #9 requires a real Forge execution implementation rather than only behavior/spec reuse, the next code slice should be a bounded extraction feasibility/implementation for the executor subsystem:

1. enumerate the exact old `executors` crate dependency closure;
2. identify which modules are genuinely executor-only and which pull product assumptions back in;
3. choose the minimal supported executor family for the first real runtime test;
4. define an execution-only transport schema subordinate to canonical `ExecutionRequest`/`ExecutionResult`;
5. record selective-port provenance for every copied/adapted source file;
6. add the optional runtime without making Rust/Forge required for Python core installation;
7. run the same `ExecutorContractSuite` plus real-runtime execute/health/cancel/evidence tests;
8. verify removal of the runtime leaves canonical persisted state and reference execution untouched.

If that extraction is judged too expensive relative to value, document the decision and complete #9 using behavior/specification reuse rather than maintaining a nominal "Forge" runtime solely for compatibility.

## Definition-of-Done interpretation

Issue #9 should not be closed merely because a class named `ForgeExecutor` exists.

It can be closed when either:

- a genuine, optional Forge-derived execution implementation is connected behind the canonical boundary and passes the required real-runtime tests; **or**
- the audit explicitly concludes that remaining Forge source-level runtime reuse is not architecture/cost justified, with all valuable mechanisms demonstrably recovered through platform-owned implementations and the unsupported runtime path explicitly rejected.

Both outcomes satisfy the underlying goal: preserve validated engineering value without inheriting the old repository as the new architecture.
