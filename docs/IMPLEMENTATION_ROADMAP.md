# Dependency-Driven Implementation Roadmap

> Status baseline: 2026-09-07, evening refresh

This roadmap describes the remaining work from current `main` toward the operational v1 baseline and the wider ideal end state. The repository is no longer in foundation construction: the canonical platform, durable workflow runtime, distributed execution path, Registry/Marketplace, release/update machinery, workflow-progress clients and the first platform-owned planning/governance/repository-intelligence implementations are present.

GitHub issue state, current issue comments and merged code remain the point-in-time source of truth. The normative product and architecture baseline remains:

- [`PRODUCT_VISION.md`](PRODUCT_VISION.md)
- [`ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md)
- accepted ADRs under [`adr/`](adr/README.md)

Issue numbers are identifiers, not an implementation sequence. Follow explicit hard dependencies before this document whenever the two diverge.

## Current maturity

### Platform baseline already reached

`main` now has concrete platform-owned implementations for:

- canonical Goal/Task/Plan/Step/Run/Event lifecycle and persistence;
- replaceable orchestration, execution, model, capability/tool, persistence and transport contracts;
- reference execution plus Hermes, Forge and LiteLLM compatibility paths;
- Control Plane, authentication, authorization/Approvals and secret references;
- Agents and Agent Teams;
- Projects, Workspaces, Files, Artifacts, Memory and Knowledge;
- Automations, Search, Verification/Review and Notifications;
- Browser, Terminal, Chat, Web UI and CLI entry points;
- Organizations/Teams/Memberships and practical Task-management metadata;
- accounting/resource attribution;
- durable Connectors and Repository/Git integration;
- reusable workflow definitions, capability assignments and model-routing profiles;
- portable import/export and Templates;
- supported single-node deployment and optional HA/failover;
- canonical Node/Worker scheduling and authenticated distributed runtime;
- network-capable MessageTransport and remote Workspace materialization;
- platform-owned durable Plan/Step coordination with waits, retries, fan-out/fan-in, cancellation, reconciliation, backup/restore, upgrades, observability and authorized repair;
- Web/CLI durable workflow-progress views through the versioned Control Plane;
- platform-owned autonomous planning/replanning foundations and exact planned Step-to-Agent runtime bindings;
- optional Proposal/Specification governance with exact-revision Approval binding and idempotent Task conversion;
- provider-neutral repository-intelligence baseline capabilities wired through the existing Repository policy boundary;
- portable host-pressure/admission foundations, observability, authenticated Worker reporting and deployment/doctor integration;
- optional Registry/Marketplace with shared canonical plugin/import ownership;
- release/update/upstream synchronization with deterministic manifest generation and reviewed evidence;
- reusable prototype and platform-wide conformance frameworks;
- substantial single-node, coordination, fault, distributed-placement and API-pressure performance evidence.

### Current open set

There are **9 open issues out of 109 repository issues**:

`#46, #388, #439, #440, #500, #501, #502, #560, #562`

That is **100 closed / 109 total = about 91.7% closed by issue count**. This is bookkeeping, not an engineering-completion percentage: the remaining issues differ greatly in scope, and #501/#502 deliberately extend the optional ideal end state beyond the operational baseline.

There are **no open pull requests** at this snapshot.

### What changed since the previous roadmap snapshot

The previous roadmap is no longer accurate in several important places:

- **#78 is closed**; Template frontend/documentation reconciliation is no longer an active lane.
- **#421 is closed via PR #540**; canonical durable workflow progress is available in Web and CLI. The narrower follow-up **#560** now owns remaining wait/retry projection semantics, coordinator-driven live refresh and final Web conformance.
- **#439 received the platform-owned planning/replanning implementation in PR #546 and a substantial runtime-completion slice in PR #559**. Exact planned Step-to-Agent execution bindings now reach the #384 runtime path. The parent issue remains open because its current completion audit still governs closure.
- **#501 received its core Proposal/Specification implementation via PR #541** but remains open for the remainder of its issue-owned Definition of Done.
- **#502 received its provider-neutral foundation and policy-enforced production wiring through PRs #539 and #556**. The remaining work is narrower and includes dirty-Workspace freshness, evaluated provider pilots, plugin/Registry packaging and resource/evaluation evidence.
- **#500 progressed through pressure/admission, observability, authenticated remote reporting and production deployment/doctor integration**; remaining issue work must be selected from the current issue rather than from the old roadmap.
- **#440 gained distributed fault and heterogeneous-placement benchmark profiles**, including the latest merged placement evidence, while wider operating-envelope work remains open.
- The old references to active PRs **#534/#536** are obsolete; both lines have already converged into `main`, and there are currently no open PRs.
- **#562** is a new production-shaped two-VPS/private-tunnel acceptance issue and must be integrated carefully with the still-open #388 transport acceptance work.

## Remaining work by ownership lane

### Lane A — #439 autonomous planning/replanning closure

#439 remains the most important open capability owner for the operational-v1 workflow path.

The merged foundation already provides:

- provider-neutral planning proposals/revisions;
- deterministic and model-backed planning paths;
- graph validation and canonical requirement checks;
- #384 activation/handoff;
- bounded replanning foundations;
- authenticated Control Plane planning operations;
- exact planned Step-to-Agent execution bindings and planned execution context reaching the Agent runtime.

Do **not** infer closure merely from the merged implementation. The issue was explicitly reopened after an earlier completion audit, and the current issue/comments remain authoritative for any still-unproven evidence-to-replan, policy-aware inventory, supersession/concurrency, evaluation, observability and required-test criteria.

Closure must prove the intended end-to-end loop rather than only the planning proposal path:

```text
Goal/Task
   -> validated canonical Plan
   -> planned Agent/Team + model/capability requirements
   -> #384 durable execution
   -> canonical Run/Verification evidence
   -> bounded replanning
   -> immutable replacement Plan
   -> #384 execution
```

#439 must not absorb #384 coordination, #14 scheduling, #10 model routing, #12 capability ownership, #15 Approval or #86 Verification.

### Lane B — #560 workflow-progress semantic completion

#560 is the follow-up to the now-closed #421 client surface. It owns the remaining client-visible coordination semantics:

- safe canonical Approval/Event/external-job wait context;
- explicit wait resolution/expiry/rejection/cancellation semantics;
- explicit retry-scheduled vs retry-exhausted semantics;
- stable attempt/budget evidence;
- reliable Web refresh for coordinator-only transitions;
- production-shaped Web/CLI parity and #46 conformance evidence.

The architecture remains:

```text
Web / CLI -> versioned Control Plane -> canonical Plan/Step coordination projection -> #384 coordinator
```

No client may read coordinator persistence or private workflow-engine state directly.

**Dependency status:** #560 currently lists **#439 as a hard dependency**. Under repository execution rules it should not be treated as an independent ready lane until #439 is closed or that dependency is explicitly revised in the issue.

### Lane C — #500 host-pressure/admission completion

#500 remains a core runtime-hardening lane. Much of its architecture is already merged:

- portable pressure model;
- pressure-aware admission without creating a second scheduler;
- #16 observability integration;
- authenticated/staleness-aware remote Worker pressure reporting;
- deployment composition and `platform doctor` visibility.

Continue only against the current issue checklist. Remaining work should preserve these invariants:

- #14 remains the sole scheduling/reservation authority;
- Linux PSI/swap/zRAM/cgroup data stays behind an optional provider boundary;
- `unknown` remains a valid portable state;
- swap/zRAM never becomes equivalent canonical physical-RAM capacity;
- pressure telemetry remains read-only by default;
- no automatic host tuning is introduced implicitly.

Once the portable/production contract is considered complete, #440 may consume it for dedicated pressure-under-load profiles.

### Lane D — #440 performance, load, stress and scalability

#440 is a mature benchmark system, not a blank foundation.

Merged evidence now includes:

- deterministic single-node lifecycle/concurrency sweeps;
- read-heavy, mixed, history and restart profiles;
- persistence growth/reopen evidence;
- idle, soak, bounded stress and restart-under-load profiles;
- transport outage/backpressure/duplicate-delivery evidence;
- Plan/Step linear, fan-out/fan-in, retry/wait and coordinator-contention pressure;
- authenticated Control Plane API/session/authorization/pagination pressure;
- distributed Worker/Workspace fault-under-load coverage;
- heterogeneous capability/resource placement benchmarks.

High-value remaining work should be selected from the current #440 checklist, especially:

- real network/cross-host operating envelopes where reproducible;
- remaining distributed fault/rejoin/materialization scale evidence;
- comparable release-sized operating envelopes and justified regression budgets;
- persistence contention/failure only through stable provider seams;
- #500 host-pressure profiles once #500's contract is complete;
- #439 planning/replanning overhead profiles once #439 closes.

Performance evidence must never weaken correctness, security, verification or durability to improve numbers.

### Lane E — #46 final platform conformance

#46 is the final convergence owner, not another subsystem implementation issue.

The repository already has a broad conformance framework and the strong same-Run vertical merged through PR #536:

```text
authenticated HTTP
 -> Control Plane
 -> Task/Run
 -> AgentRun/model
 -> ToolInvocation/Capability
 -> Executor
 -> Worker/Node
 -> remote Workspace
 -> File/Artifact
 -> Verification
 -> accepted Task
 -> canonical API/timeline/observability
```

Keep #46 open while remaining claimed core capabilities converge. Its final audit should consume, where applicable:

- #439 end-to-end planning/replanning evidence;
- #560 workflow-client parity and live-progress semantics;
- #500 pressure-aware evidence only for profiles that claim it;
- #440 operating-envelope evidence for release claims;
- #388/#562 real-host evidence only for releases claiming the corresponding cross-host profile;
- explicit `supported`, `disabled`, `unsupported` or `not-run` outcomes for optional profiles rather than false compatibility claims.

#46 should close from an exact passing candidate/evidence set, not because individual subsystem tests happen to be green separately.

### Lane F — #388 and #562 real distributed acceptance

#### #388 — remaining transport acceptance

The network-capable MessageTransport implementation exists. The remaining #388 criteria are explicit real-host evidence:

- encrypted/authenticated two-host operation;
- canonical Worker identity preserved across restart/reconnect;
- result retrieval across the real network path;
- canonical Artifact/evidence references surviving that return path.

This is mostly an operational acceptance exercise rather than a transport redesign.

#### #562 — real two-VPS private-tunnel deployment

#562 expands the real-host acceptance into a production-shaped topology:

```text
Client -> Control Plane/Scheduler host -> private tunnel -> remote Node/Worker -> canonical result/reconciliation
```

It covers private connectivity, authenticated registration/heartbeats, capability-based dispatch, deliberate tunnel interruption, liveness/reconciliation, tunnel restoration, Worker restart and sanitized acceptance evidence.

There is an important dependency inconsistency to resolve before treating #562 as an ordinary ready lane: **#562 currently lists #46 as a hard dependency while also requiring its result to be recorded as #46 conformance evidence.** Under `AGENTS.md`, hard dependencies are expected to be merged/closed before work starts. Either the issue should explicitly mean “existing #46 conformance framework” rather than “#46 must be closed”, or the dependency should be reclassified. Do not silently invent a different rule in implementation branches.

Where the two issues overlap, a well-designed #562 real-host run should be reusable as evidence for #388, but each issue should still be closed only against its own acceptance criteria.

### Lane G — optional ideal-end-state expansion: #501 and #502

These remain optional and must not become hidden blockers for the ordinary baseline.

#### #501 — Proposal/Specification governance

The merged core already supports versioned Proposal intake, immutable Specification revisions/digests, exact-revision Approval binding, idempotent Task conversion, canonical Control Plane/Search/audit surfaces, Web/CLI integration and exact-revision planning input.

Continue by auditing the current #501 acceptance checklist against merged behavior and implementing only genuinely missing issue-owned gaps. Preserve:

```text
ordinary path: Task -> Plan/Run
optional governed path: Idea/Signal -> Proposal -> Specification -> Approval -> Task
```

Proposal/Specification must never become a second Task or execution lifecycle.

#### #502 — repository/code intelligence

The deterministic baseline and production Repository-policy wiring are merged. Remaining work includes the parts explicitly left open by the merged foundation:

- dirty-Workspace/source-snapshot freshness semantics;
- fresh evaluation of maintained third-party candidates;
- isolated provider pilots where a candidate passes source/license/security review;
- plugin packaging and optional Registry metadata;
- resource/evaluation evidence;
- planner/reviewer context-funnel integrations only when they consume stable canonical contracts.

The fallback remains:

```text
optional intelligence provider unavailable/stale
 -> Git + ripgrep + LSP / canonical Repository baseline
 -> work continues
```

No named intelligence project becomes canonical architecture.

## Safe parallel work now

Based on current hard dependencies, the useful work split is:

| Track | Owner | Ready now? | Main collision risk |
|---|---|---:|---|
| A | #439 planning/replanning closure | Yes | planning services, #384 handoff, Control Plane |
| B | #500 pressure/admission completion | Yes | scheduler/Node reporting/observability/deployment |
| C | #440 benchmark expansion | Yes | benchmark/runtime/distributed fixtures |
| D | #501 completion audit/integrations | Yes | Approval/Search/Control Plane/Web |
| E | #502 intelligence completion | Yes | Repository/Search/plugin/capability surfaces |
| F | #388 real two-host transport acceptance | Yes, with environment | distributed operator fixtures/evidence |
| G | #46 continuing conformance audit/evidence | Yes, but final closure waits on claimed core work | conformance/CI/release evidence |
| H | #560 workflow semantic follow-up | **No under current hard dependency** | waits/retries/client projections |
| I | #562 two-VPS private-tunnel validation | **Dependency wording must be resolved** | operator topology/conformance evidence |

For active coding, prefer roughly **five focused implementation branches at once** rather than maximizing nominal concurrency. #388 real-host execution and #46 evidence/audit can run as separate operational/acceptance work when they do not collide with those code branches.

## Recommended immediate sequencing

1. **Drive #439 to its actual current Definition of Done.** PR #559 materially reduced the gap, but the issue should close only after the reopened completion audit is satisfied.
2. **Continue #500 and #440 in parallel.** Keep #440's #500-specific pressure profiles behind the completed/stable pressure contract.
3. **Continue #501 and #502 only on their remaining issue-owned gaps.** Do not reimplement already merged foundations.
4. **Run #388 real two-host acceptance when the environment is available.** Reuse sanitized evidence later where #562/#46 require the same facts.
5. **After #439 closes, start/finish #560** under its current hard-dependency contract, then feed client-parity evidence into #46.
6. **Resolve #562's #46 hard-dependency wording before scheduling it as a normal lane.** Avoid a circular “#562 waits for #46 while #46 waits for #562 evidence” interpretation.
7. **Close #46 last for the operational profile actually claimed by the release**, with exact release-candidate checks and explicit optional-profile status.

## Dependency picture

```text
                         current main
                              |
        +----------+----------+----------+----------+
        |          |          |          |          |
      #439       #500       #440       #501*      #502*
        |          |          |          |          |
        v          +-----> #440          |          |
      #560           pressure profiles   |          |
        |                                 |          |
        +---------------+-----------------+----------+
                        |
                        v
                      #46 final convergence

#388 real-host transport acceptance -----------+
                                                |
#562 real two-VPS acceptance -- dependency -----+--> optional distributed claim
                                wording to resolve

* #501/#502 are optional ideal-end-state extensions and do not block the ordinary baseline.
```

#46 and #440 accumulate evidence continuously; the diagram shows final convergence, not a demand that they remain idle until every upstream issue closes.

## Progress interpretation

Use separate progress views rather than one misleading “percent complete” number.

### Repository issue-count progress

**100 / 109 issues closed = about 91.7%.**

This is objective bookkeeping but ignores issue size, reopened completion audits and optional scope expansion.

### Operational-v1/core maturity

The foundational architecture, durable workflows, distributed runtime, Registry, release/update lifecycle, workflow-progress clients and most product domains are implemented. The remaining high-weight core work is concentrated in #439, #500, #560, the remaining #440 evidence and final #46 convergence.

That means the project is in **late convergence**, but it is not release-ready merely because the issue-count percentage is high. Release readiness is determined by exact acceptance evidence.

### Expanded ideal-end-state maturity

#501/#502 and real-host distributed acceptance extend the target beyond the minimal operational baseline. Their remaining work should be tracked separately so optional scope growth does not silently redefine the v1 release gate.

## Release interpretation

No GitHub release is currently published. The release/update system itself (#42) is complete, including deterministic manifest generation and reviewed update evidence. Publication still requires the repository release process, exact evidence/manifest, changelog/provenance and a passing release commit.

The operational `1.0.0` target should remain tied to the supported M3 profile and #46 acceptance. Optional ecosystem features must not become hidden release blockers unless the release explicitly claims those profiles. Real two-host compatibility must likewise be claimed only after its explicit acceptance run passes.

## Consistency rules for every remaining issue

Every implementation must continue to preserve:

- one canonical owner per lifecycle/resource;
- no backend/provider/private identity promoted to canonical state;
- hard dependencies separated from progressive integrations;
- provider/model/hardware/deployment neutrality in canonical contracts;
- self-hostable reference behavior without recurring paid AI/API dependencies;
- authorization, Approval and Verification as authoritative platform boundaries;
- restart/idempotency/deduplication semantics for durable state;
- backend-neutral Control Plane projections for clients;
- tests proportional to the actual failure mode;
- explicit unsupported/disabled/not-run reporting instead of false compatibility claims;
- no acceptance-only shortcut that hides missing production behavior.

When this roadmap and GitHub diverge, current issue wording/comments and merged code are authoritative until the roadmap is refreshed again.
