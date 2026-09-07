# Dependency-Driven Implementation Roadmap

> Status baseline: 2026-09-07

This roadmap describes the remaining work from current `main` toward the operational v1 baseline and the wider ideal end state. The repository is no longer in foundation construction: the canonical platform, durable workflow runtime, distributed execution path, Registry/Marketplace and release/update machinery are implemented. The remaining work is now concentrated in product-facing workflow/planning capabilities, final conformance, performance/operating-envelope evidence, host-pressure admission, a very small Template reconciliation, one real two-host transport acceptance gap, and optional governance/repository-intelligence extensions.

GitHub issue state, current issue comments and merged code remain the point-in-time source of truth. The normative product and architecture baseline remains:

- [`PRODUCT_VISION.md`](PRODUCT_VISION.md)
- [`ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md)
- accepted ADRs under [`adr/`](adr/README.md)

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
- optional Registry/Marketplace with shared canonical plugin/import ownership;
- release/update/upstream synchronization with deterministic manifest generation and reviewed evidence;
- a reusable prototype gate and platform-wide conformance framework;
- substantial single-node, coordination, fault and API-pressure performance evidence.

### Current open set

There are **9 open issues out of 105 repository issues**:

`#46, #78, #388, #421, #439, #440, #500, #501, #502`

That is **96 closed / 105 total = about 91.4% closed by issue count**. This is useful as a repository bookkeeping metric, but it is not a literal engineering-completion percentage: #439, #421, #500, #440 and #46 are much higher-weight than a typical small hardening issue, while #501/#502 deliberately expand the optional ideal end state after earlier platform foundations were already complete.

### Completed since the previous snapshot

Four issues that were previously major frontier items are now closed:

- **#42** — release/update/upstream synchronization is operationalized, including deterministic release-manifest generation, durable reviewed discovery reports and stronger adoption evidence/pin consistency;
- **#81** — Registry/Marketplace is complete after the final installation, update, connector-inventory, pinning and restart/plugin-lifecycle hardening;
- **#240** — distributed/heterogeneous deployment is complete after the production operator path, Worker isolation, secure entry-point acceptance and canonical Task/Run distributed integration were hardened;
- **#384** — the durable Plan/Step coordinator is complete and accepted, including the final Worker acknowledgement/cancellation, orchestrator replacement, repair and conformance evidence.

This changes the dependency graph materially: **#421 and #439 are no longer waiting on #384**, and distributed #440 evidence no longer waits on #240.

## Remaining work by ownership lane

### Lane A — Final operational-v1 convergence

The M3/core endgame is now centered on:

```text
#78 tiny closure          #421 workflow clients
       \                  /
        \                /
         +--> #46 <------+----- #439 autonomous planning
        /        ^        \
       /         |         \
#440 performance |          #500 host pressure/admission
                 |
          completed runtime foundations
```

The exact dependency arrows are not all hard blockers: #46 and #440 can accumulate evidence continuously. The diagram shows convergence ownership, not a strict serial schedule.

#### #78 — Templates: final two-gap reconciliation

The Template domain, security, compatibility, materialization, trust/activation, rollback, Workflow/Capability Assignment/model-routing integration, create-from-existing surface and owner-domain navigation work are already implemented.

The latest completion audit leaves only two explicit #78-owned gaps:

1. safe read-only owner-domain links/detail routes for Template-created Workflow, Capability Assignment and Model Routing Profile resources;
2. `docs/FRONTEND.md` reconciliation so create-from-existing documentation lists those newer supported resource types.

Treat #78 as a **small closure task**, not as another Template redesign. It can and should be finished independently from #421/#439.

#### #421 — Web/CLI durable workflow progress

#384 is closed, so #421 is now fully unblocked.

Implement one canonical client surface over the existing Control Plane projections for:

- active Plan revision and Plan/Step IDs;
- dependency/progression state;
- current/latest Run attempts;
- waits/deadlines;
- retries/exhaustion;
- fan-out/fan-in/barrier progress;
- cancellation propagation;
- reconciliation/repair disposition;
- safe canonical Event/timeline evidence;
- matching human-readable and JSON CLI views.

This work must remain `Web/CLI -> Control Plane -> canonical coordinator projection`; clients must not read coordinator persistence directly.

#### #439 — Autonomous planning and bounded replanning

#439 is the largest genuinely new core capability still open and is now also fully unblocked by #384.

It owns:

`Goal/Task intent -> Planner -> validated canonical Plan/Steps -> #384 execution -> canonical evidence -> bounded replanning`

The first implementation should establish:

- replaceable planner contract;
- deterministic/reference planner fixture;
- model-backed planner through canonical model APIs;
- Agent/Team, capability and model-requirement resolution;
- graph validation and satisfiability checks;
- safe parallel/dependency decomposition;
- immutable Plan revisions;
- explicit replanning triggers from canonical failure/Verification/evidence;
- completed-work reuse with provenance;
- idempotent/restart-safe bounded replanning;
- Control Plane projections/commands and evaluation coverage.

Do not let #439 absorb #384 durable execution, #14 scheduling, #10 routing, #12 capability ownership, #15 Approval or #86 Verification.

#### #500 — Host resource pressure telemetry and admission control

#500 is a **v1 runtime issue**, not merely an optional ecosystem enhancement. It can start immediately because its hard foundations (#14 scheduler, #16 observability, #32 Control Plane, #39 single-node deployment) already exist.

Split it into a portable core plus platform-specific adapters:

- portable pressure snapshot (`healthy/elevated/critical/unknown` plus normalized evidence);
- deterministic pressure-aware admission hook that augments but never replaces #14 scheduling;
- configurable protected-headroom policy;
- authenticated/staleness-aware Node/Worker pressure reporting;
- Linux PSI/swap/zRAM/cgroup reference provider behind an optional adapter boundary;
- #16 telemetry and diagnostics;
- deployment hooks/docs;
- later #440 pressure benchmarks.

Linux-specific measurements must remain provider metadata; unsupported pressure reporting must stay explicit rather than making non-Linux Workers unusable.

### Lane B — #46 final platform conformance

#46 is now a convergence/closure lane rather than a framework-construction lane.

Already merged evidence covers the authenticated Control Plane, Task/Run lifecycle, Agent/Model execution, native Capability invocation, canonical ToolInvocation identity, Executor/Worker lineage, many product domains, #384 coordinator conformance and release-profile registration.

The strongest remaining continuous vertical is being closed by active PR **#536**, targeting:

`authenticated HTTP -> Task/Run -> AgentRun -> local model -> ToolInvocation -> Capability -> Executor -> Worker/Node -> remote Workspace change -> canonical File -> distinct canonical Artifact -> same Run -> Verification -> Task completion`

After that production path lands, #46 should perform a final issue-wide audit rather than simply close because individual subsystem tests are green. Final closure should verify:

- the strongest maintained vertical crosses the intended layers without a shadow lifecycle;
- required release scenarios are executable/compatible;
- optional profiles are honestly reported as compatible, disabled, unsupported or not implemented;
- canonical IDs/ownership remain intact across the final Agent/Tool/Worker return path;
- #421 workflow-client parity and #439 planning/replanning evidence are added when those v1 features land;
- #500 pressure-aware conformance is claimed only where actually enabled/tested;
- all required CI, security/static, conformance, prototype and relevant real-adapter checks pass on the exact release candidate.

**Recommendation:** keep #46 open as the last core convergence issue rather than closing and reopening it after each newly completed M3 capability.

### Lane C — #440 performance, load, stress and scalability

#440 is already a mature benchmark system rather than an empty foundation.

Merged evidence includes:

- deterministic single-node lifecycle/concurrency sweeps;
- read-heavy, mixed and large-state/restart profiles;
- persistence growth/reopen measurements;
- idle/soak/stress/restart-under-load profiles;
- provider/transport fault evidence;
- Plan/Step linear, fan-out and fan-in scale;
- retry/wait/restart coordination pressure;
- concurrent multi-Plan and coordinator claim contention;
- authenticated Control Plane API/session/authorization/pagination pressure.

Active PR **#534** adds the first distributed Worker/remote Workspace scale profile.

After #534, remaining high-value work is:

- TCP/cross-host distributed operating envelopes where stable and reproducible;
- Worker loss/rejoin and remote Workspace failure under load;
- persistence-failure/contention evidence only through a stable provider/fixture seam, not a SQLite-specific production bypass;
- release-sized comparable operating-envelope runs and initial regression budgets;
- optional HA/live-update profiles where justified;
- #500 host-pressure/admission benchmark family after the portable pressure contract exists;
- #439 planner latency/activation/replanning overhead after the planner is implemented.

#440 can run in parallel with #421/#439/#500; only its feature-specific profiles should wait for the corresponding owner contract.

### Lane D — #388 real two-host transport acceptance

#388 was reopened by a completion audit. The TCP/network transport implementation already exists; the remaining work is evidence, not redesign.

Before re-closing it, prove on **two independent hosts**:

- authenticated encrypted Control Plane/Worker transport;
- dispatch and result retrieval;
- restart/reconnect without changing canonical Worker identity;
- canonical artifact/evidence references surviving the real network return path;
- recorded acceptance evidence plus green validation.

This track is operationally independent from most core coding work and is an excellent parallel task when a two-host test environment is available. Do not substitute same-host sockets/processes for the explicit two-host criterion.

### Lane E — Optional ideal-end-state expansion: #501 and #502

These issues are intentionally optional M4-style extensions. Their absence must not break the direct Task path or the ordinary repository/tool baseline.

#### #501 — Proposal/Specification governance

Build an optional intake path:

`Idea/signal -> Proposal -> Specification -> exact-revision Approval -> canonical Task`

The **core Proposal/Specification resource model, persistence, Approval binding and idempotent Task conversion can start now in parallel** with #439.

Defer only the deeper planner-specific integration until #439's planner contract stabilizes. #439 may later consume an approved Specification or help draft one, but it must never own or silently rewrite Specification truth.

#### #502 — Repository/code intelligence providers

Build from the deterministic fallback first:

`Git + ripgrep + LSP -> measured baseline -> optional provider evaluation -> plugin/capability integration -> optional Marketplace catalog`

The baseline harness, capability taxonomy, security/resource classification and candidate evaluation can start immediately. Do not wait for #439.

Defer only planner-specific context-funnel integration until #439 is stable, and use #500 for heavy indexing/admission integration once its pressure contract exists. No named provider becomes canonical architecture.

## Safe parallel work now

A high-throughput current split is:

| Track | Owner | Can start now? | Main collision risk |
|---|---|---:|---|
| A | #78 final two-gap closure | Yes | frontend routes/docs |
| B | #46 / PR #536 continuous vertical + final audit | Yes | distributed/File/Artifact/conformance files |
| C | #440 / PR #534 distributed benchmark | Yes | distributed test/runtime harness |
| D | #421 Web/CLI workflow progress | Yes | Control Plane/client registration |
| E | #439 planner/replanning | Yes | Task/Plan services and Control Plane |
| F | #500 pressure/admission core | Yes | #14 scheduler/Node reporting/observability |
| G | #388 real two-host acceptance | Yes, with environment | distributed operator fixtures/docs |
| H | #501 Proposal/Specification core | Yes | Control Plane/Search/Approval surfaces |
| I | #502 repository-intelligence baseline/evaluation | Yes | Repository/Search/plugin capability surfaces |

With disciplined branches, **7–9 focused tracks can make useful progress concurrently**. More concurrency is not automatically better: the files most likely to collide are Single-Node/deployment composition, Control Plane resource registration, shared frontend routing/manifest code, distributed runtime helpers and conformance/CI documents.

## Recommended immediate sequencing

### Immediate closure first

Before opening many cross-cutting branches, prefer to land these small/currently active changes quickly:

1. finish #78's two explicit gaps;
2. finish and validate PR #536 for the #46 same-Run Worker Artifact -> Verification vertical;
3. finish and validate PR #534 for the first distributed #440 scale profile.

This reduces overlapping frontend/distributed/conformance edits while the larger new tracks continue separately.

### In parallel immediately

Start #421, #439 and #500 as independent core lanes. In parallel, #501 and #502 may begin their owner-domain/baseline work because they do not need #439 to define their canonical resources. Run #388 acceptance whenever the two-host environment is available.

## What becomes newly parallel after #439

Once the planner contract and first working planning/replanning path are stable, several follow-ups unlock simultaneously:

| Track | Newly unlocked work |
|---|---|
| P1 | #46 Goal/Task -> generated Plan -> durable execution -> Verification -> bounded replan conformance |
| P2 | #440 planner latency, Plan activation and replanning overhead benchmarks |
| P3 | #501 approved Specification -> planner input and optional draft-Spec integration |
| P4 | #502 planner repository-intelligence/context-funnel integration |
| P5 | optional Template/planner-policy packaging where it remains canonical and portable |

These should not be serialized behind one another; they all consume #439's stable public contract.

## What becomes newly parallel after #500

Once the portable pressure/admission contract is stable:

| Track | Newly unlocked work |
|---|---|
| R1 | #440 PSI/swap/zRAM/cgroup/admission-under-pressure benchmark profiles |
| R2 | distributed Node/Worker pressure-report integration and acceptance |
| R3 | #502 resource-aware admission for heavy indexing/rebuild workloads |
| R4 | #46 pressure-aware profile evidence if the release chooses to claim it |

Again, these are consumers of #500; they should not force Linux-specific fields into the canonical contract.

## What becomes newly parallel after #421

Once Web/CLI workflow progress is complete:

- #46 can add explicit coordinator client-parity evidence;
- #501 can link Proposal/Specification conversions cleanly into Task/Plan progress views without creating a second Task board;
- future planner/proposal UX can reuse the same canonical Task/Plan navigation instead of creating client-private workflow state.

## Endgame dependency picture

```text
                         current main
                              |
      +-----------+-----------+-----------+-----------+
      |           |           |           |           |
     #78         #421        #439        #500        #388
      |           |           |           |           |
      |           |     +-----+-----+     |           |
      |           |     |           |     |           |
      |           |   #501*       #502*   |           |
      |           |   integrate   integrate|           |
      |           |     |           |     |           |
      +-----------+-----+-----------+-----+-----------+
                              |
                    #440 accumulated evidence
                              |
                              v
                       #46 final audit
                              |
                              v
                  operational v1 acceptance

* #501/#502 remain optional; their absence must not invalidate the ordinary baseline.
```

#46 and #440 run continuously rather than waiting idle at the bottom. The diagram shows when their **final claims** should converge.

## Progress interpretation

Use three separate progress views rather than one misleading number:

### 1. Repository issue-count progress

**96 / 105 issues closed = 91.4%.**

This is objective bookkeeping but ignores issue size and optional scope expansion.

### 2. Operational-v1/core maturity

The foundational architecture, durable workflows, distributed runtime, Registry, release/update lifecycle and most product domains are implemented. The remaining high-weight core work is concentrated in #421, #439, #500, the remaining #440 evidence and final #46 convergence, with #78 almost entirely complete.

A reasonable planning heuristic is therefore **roughly 88–92% core maturity**, not because that percentage can be measured exactly, but because the remaining work is now a small number of substantial end-product capabilities rather than missing foundational domains.

### 3. Expanded ideal-end-state maturity

If the optional #501 Proposal/Specification layer, #502 repository-intelligence ecosystem and full #388 two-host acceptance are counted as part of the target denominator, the maturity estimate should be lower: **roughly 82–87%**. These issues add genuine new scope after much of the earlier platform had already been completed.

These heuristic ranges are for planning only. Release readiness is determined by explicit acceptance evidence and #46, not by a percentage.

## Release interpretation

No GitHub release is currently published. The release/update system itself (#42) is complete, including deterministic manifest generation and reviewed update evidence. Publication still requires the repository release process, exact evidence/manifest, changelog/provenance and a passing release commit.

The operational `1.0.0` target should be tied to the supported M3 profile and #46 acceptance. Optional M4 features such as #501/#502 must not become hidden release blockers unless the release explicitly claims those profiles. #388 likewise governs the strength of a true cross-host compatibility claim rather than the validity of the ordinary single-node baseline.

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
- explicit unsupported/disabled reporting instead of false compatibility claims;
- no acceptance-only shortcut that hides missing production behavior.

When this roadmap and GitHub diverge, current issue wording/comments and merged code are authoritative until the roadmap is refreshed again.
