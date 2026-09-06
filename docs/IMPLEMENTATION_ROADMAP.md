# Dependency-Driven Implementation Roadmap

> Status baseline: 2026-09-06, after PR #493

This roadmap describes the remaining work from the current `main` branch toward the ideal end-state platform. The repository is no longer in its foundation-building stage: most canonical domains and the usable single-node product path are implemented. The remaining work is concentrated in late cross-domain integration, distributed deployment hardening, durable workflow productization, autonomous planning, performance evidence, release operationalization and final platform conformance.

GitHub issue state and the current wording of each issue remain the point-in-time source of truth. The normative product and architecture baseline remains:

- [`PRODUCT_VISION.md`](PRODUCT_VISION.md)
- [`ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md)
- accepted ADRs under [`adr/`](adr/README.md)

## Current maturity

### Broad platform baseline reached

The repository now has concrete implementations covering:

- product/repository/provenance foundations;
- canonical Task/Plan/Step/Run/Event domain and Task/Run kernel;
- replaceable orchestration, execution, model, capability/tool and persistence/provider contracts;
- reference execution plus Hermes, Forge and LiteLLM compatibility paths;
- Control Plane, authentication, authorization/approvals and secret-reference handling;
- Agents and Agent Teams;
- Projects, Workspaces, Files, Artifacts, Memory and Knowledge;
- Automations, global Search, Verification/Review and Notifications;
- Browser, Terminal, Chat, Web UI and CLI client paths;
- Organizations/Teams/Memberships and practical Task-management metadata;
- accounting and resource-attribution integrations;
- durable Connector persistence and Repository/Git integration;
- reusable workflow definitions and portable import/export;
- supported single-node deployment plus optional Control Plane HA/failover;
- canonical Node/Worker scheduling, network-capable transport and remote Workspace materialization;
- reusable no-paid-service prototype acceptance (#252);
- performance benchmark infrastructure and multiple production-shaped profiles;
- release/provenance/compatibility infrastructure;
- a platform-wide conformance framework with required and optional evidence profiles.

The latest completed hardening wave also closed #171, #414, #416 and the routing-profile follow-up cluster #443–#447.

### Current open set

There are currently **9 open issues out of 98 repository issues**:

`#42, #46, #78, #81, #240, #384, #421, #439, #440`

There are currently **no open pull requests**.

The raw issue-closure ratio is therefore about **90.8%**, but the remaining issues are disproportionately large. The repository should be treated as a late-stage platform with several substantial end-state capabilities and acceptance gates still open, not as a product that is literally 90.8% complete by engineering effort.

## What changed since the previous roadmap snapshot

The previous roadmap listed 17 open issues and an open #240 PR. That snapshot has been overtaken by the following merged work:

- #443–#447 routing-profile management/invariant/assignment/provenance/compensation hardening completed;
- #414 canonical Node/Worker state-change timestamps completed;
- #416 restart-durable Connector persistence completed;
- #171 remaining accounting integration gaps completed;
- #384 received the platform-owned durable Plan/Step coordinator implementation through PR #472;
- #240 received the real distributed deployment integration path through PR #487;
- #78 received final canonical Workflow/Capability Assignment/model-routing Template integrations and create-from-existing surfaces through PR #481;
- #81 received owner-domain activation and northbound CLI/frontend integration through PRs #478 and #488;
- #440 gained single-node sweeps, read/mixed/history/restart profiles, idle/soak, bounded stress, restart-under-load and transport degradation/recovery profiles;
- #42 gained the release/update baseline plus provenance/compatibility hardening through PRs #461 and #492;
- #46 gained the conformance foundation, required release scenarios, optional compatibility profiles, release lifecycle evidence and an authenticated production-shaped vertical slice.

The remaining plan must therefore start from these merged capabilities rather than replay the old implementation waves.

## Remaining dependency lanes

### Lane A — Completion and acceptance audits: #78, #81, #384

These three issues now have substantial or near-complete implementations on `main`, but they remain open and must be closed by verifying their current Definition of Done against the merged state rather than assuming a merge automatically proves every acceptance item.

#### #78 Templates

Recent work completed canonical Workflow and Capability Assignment integration, model-routing-policy activation, create-from-existing coverage, Single-Node composition and frontend/API exposure. The immediate next action is a current-state acceptance audit against the issue's reopened gaps and required regressions.

If that audit finds no remaining production gap, close #78. If it finds a real gap, extract only the concrete missing work rather than reopening already completed integration slices.

#### #81 optional Registry / Marketplace

The provider-neutral Registry foundation, canonical owner handoff and CLI/frontend client hooks are merged. PR #488, which the issue body still lists as pending, is already merged. The remaining work is therefore primarily final CI/current-main acceptance reconciliation and issue closure, unless the audit identifies a genuine missing contract or safety path.

#81 remains optional and must never become a startup/runtime dependency for the ordinary self-hosted platform.

#### #384 durable Plan/Step coordinator

PR #472 merged the platform-owned coordinator covering durable coordination records, dependency progression, fan-out/fan-in, waits, Step retries, cancellation, restart reconciliation, optimistic revisions/claims, Control Plane projections and telemetry.

Because #384 remains open, perform a final requirement-by-requirement audit around the difficult crash/reconciliation, backup/restore, fencing and acceptance cases. Do not treat the merged core as permission to weaken the platform-owned lifecycle boundary.

### Lane B — Workflow product layer unlocked by #384: #421 and #439

Once #384's backend-neutral projections are treated as stable enough for consumers, two major tracks can progress **in parallel**.

```text
                 #384 durable coordinator
                         |
              +----------+----------+
              |                     |
              v                     v
     #421 Web/CLI progress     #439 planning/replanning
              |                     |
              +----------+----------+
                         |
                         v
                   #46 conformance
```

#### #421 Web/CLI workflow progress

Expose canonical Plan/Step progression, Runs, waits, retries, fan-in state, cancellation and reconciliation through the existing Control Plane. This is client work over #384; it must not read coordinator stores directly or invent a frontend lifecycle.

#### #439 autonomous goal decomposition, planning and bounded replanning

This is the largest genuinely new end-state capability still open. It owns:

`Goal/Task intent -> planning provider -> validated canonical Plan/Steps -> #384 execution -> canonical evidence -> bounded replanning`

It can be developed independently from #421 once the #384 contract is stable. It must not absorb Worker scheduling, model routing, authorization, Verification or durable Step progression.

### Lane C — Distributed and heterogeneous deployment hardening: #240

The architecture prerequisites are no longer missing:

```text
#14 Node/Worker runtime
#35 MessageTransport
#36 service identity
#37 Workspace contracts
#433 remote materialization
        |
        v
#240 distributed deployment integration (major path merged)
```

PR #487 routes ordinary canonical Task/Run execution through the distributed runtime when the advanced deployment is enabled, shares the scheduler/runtime composition, propagates exact Workspace bindings and proves an authenticated HTTP Task reaching a registered distributed Worker.

The issue remains open for focused final hardening, including the residual acceptance items explicitly recorded by the merged work such as zero-byte Workspace transfer coverage, positive TLS/mTLS evidence, documentation synchronization and final deployment-profile acceptance.

Do not expand #240 into HA, cloud provisioning or a second scheduler. Those boundaries are already owned elsewhere.

### Lane D — Performance and scalability evidence: #440

#440 is no longer a benchmark-foundation issue. The following substantial profiles are already merged:

- deterministic single-node lifecycle benchmark;
- concurrency sweeps;
- read-heavy and mixed read/write workloads;
- large-history and restart workloads;
- idle-footprint and soak/endurance profiles;
- bounded saturation/stress and restart-under-load;
- transport backpressure, outage/recovery and duplicate-delivery evidence.

The newly unlocked high-value work is now:

1. **Plan/Step profiles** using the merged #384 coordinator: long linear plans, 10/100/1000-step graphs where practical, wide fan-out/fan-in, waits/retries under load and restart/reconciliation under workflow load.
2. **Distributed profiles** using the merged #240/#433 path: multiple local Workers, remote Worker dispatch, N-worker sweeps, Worker heartbeat/registration load, Workspace materialization payload sweeps and Worker loss/rejoin under load.
3. **Remaining deterministic faults** where stable seams exist: transient persistence failure, model/tool/provider unavailability, Workspace materialization failure and optional HA promotion.
4. **Comparable operating envelopes and regression budgets** based on measured evidence rather than guessed universal limits.

The workflow and distributed #440 tracks can run in parallel once their respective owning paths are accepted.

### Lane E — Release/update operationalization: #42

The release/update foundation and hardening are already merged. #42 now has typed release manifests, compatibility inventory, upstream pins, evidence gates, provenance validation, advisory update discovery and operator/read-only status surfaces.

The current issue body identifies the remaining operationalization work:

- deterministic release-manifest generation/workflow integration;
- persistent advisory discovery reports wired into operator/runtime/UI state after restart;
- explicit frontend typing for the richer release schema v2 structures;
- optional provider-neutral scheduled discovery adapters where useful, while preserving manual adoption and the no-recurring-paid-service constraint.

This lane is largely independent from #421/#439/#240 and can progress continuously in parallel.

### Lane F — Final platform convergence: #46

#46 already has a real conformance framework, fast/integration/release profiles, required single-node release evidence, optional compatibility claims, release lifecycle checks and a production-shaped authenticated vertical slice.

It must remain the **consumer of completed production behavior**, not the place where missing product behavior is faked for tests.

Immediate expandable evidence now includes:

- Templates/Q after #78 acceptance;
- Registry/S after #81 acceptance;
- durable Plan/Step workflow evidence after #384 acceptance;
- stronger distributed evidence after #240 acceptance;
- workflow/planning coverage after #421/#439;
- performance/release evidence from #440/#42.

The current authenticated vertical slice still explicitly separates Agent/Model execution from the ReferenceExecutor/Worker path. Final conformance must keep that gap visible until a production-valid cross-layer path or explicitly separated canonical scenario set proves the intended architecture without test-only shortcuts.

#46 is the convergence gate and should close last among the core 1.0-target issues.

## Safe parallel work now

A practical high-throughput split from current `main` is:

| Track | Work | Dependency status |
|---|---|---|
| A | #384 final DoD / crash-recovery-backup audit | Core implementation merged |
| B | #240 distributed deployment hardening | Major integration path merged |
| C | #42 release/update operationalization | Independent |
| D | #440 remaining workflow/distributed/fault profiles | Partially unlocked now |
| E | #78 final Template acceptance audit | Major integrations merged |
| F | #81 final Registry acceptance audit | Northbound integration merged; coordinate with #78 findings |
| G | #46 add only already-supported conformance evidence | Continuous convergence lane |

This means **5–7 focused agents** can work safely if branch overlap is managed. The main collision risks are shared Single-Node composition, Control Plane registration, release/conformance CI files and common documentation.

## Next parallel expansion after #384 acceptance

Once #384's final audit confirms its public projections/contracts are stable, start these simultaneously:

| Track | Issue/work |
|---|---|
| W1 | #421 Web/CLI workflow-progress surfaces |
| W2 | #439 autonomous planning and bounded replanning |
| W3 | #440 Plan/Step performance profiles |
| W4 | #46 durable-workflow conformance evidence |

#421 and #439 do not depend on each other. They share #384 as a foundation and should coordinate only where both extend Task/Plan client projections.

## Next parallel expansion after #240 acceptance

Once the distributed deployment profile passes its final acceptance:

| Track | Work |
|---|---|
| D1 | #440 distributed Worker/load/materialization benchmarks |
| D2 | #46 distributed conformance strengthening |
| D3 | deployment/release evidence integration for #42 where applicable |

These can proceed alongside the workflow/product tracks above.

## Final convergence order

A dependency-safe endgame is:

```text
Current main
   |
   +--> audit/close #78
   +--> audit/close #81
   +--> audit/close #384
   +--> finish #240
   +--> continue #42
   +--> continue #440
             |
             +-----------------------------+
             |                             |
             v                             v
        #421 Web/CLI                  #439 Planner
             |                             |
             +--------------+--------------+
                            |
                #440 remaining workflow /
                  distributed evidence
                            |
                            v
                     #42 release-ready
                            |
                            v
                   #46 final conformance
                            |
                            v
                 operational 1.0 baseline
```

This is not a strict serial pipeline: #42, #440 and supported portions of #46 should continue throughout. The serial constraints are primarily that clients/planning must not outrun unstable #384 contracts, distributed benchmarks must not substitute for unfinished #240 deployment behavior, and final #46 compatibility claims must not precede the owning production paths.

## Progress interpretation

Two different progress measures should be kept separate:

- **Issue-count progress:** 89 of 98 issues closed, about 90.8%.
- **End-state engineering maturity:** lower than the raw issue ratio because #439, #46, #440, #42 and the remaining #240/#384 work are high-weight platform capabilities and acceptance gates.

The project is therefore best described as **late-stage architecture/product integration with most canonical subsystems implemented**, while autonomous planning, final workflow/distributed productization and full release/conformance evidence remain the main path to the ideal end state.

## Release interpretation

The planned `0.1.0` prerequisite (#252) has passed, but no GitHub release is published yet. A formal prototype release still requires the publication checklist in [`RELEASE_PROCESS.md`](RELEASE_PROCESS.md), current changelog/provenance and an exact passing release commit.

The planned `1.0.0` operational baseline remains tied to #46 full conformance. Optional Registry operation must not become a hidden prerequisite for the local/self-hosted baseline, even if #81 is completed before 1.0.

## Consistency rules for every remaining issue

Each implementation must continue to preserve:

- exactly one canonical owner and no competing private contract;
- explicit hard dependencies separated from follow-up integrations;
- provider/model/hardware/deployment neutrality in canonical state;
- self-hostable reference behavior without a recurring paid AI/API dependency;
- security and Verification as authoritative platform boundaries rather than planner/orchestrator hints;
- restart/deduplication semantics where durable state is involved;
- backend-neutral Control Plane projections for Web/CLI clients;
- tests proportional to the failure mode being closed;
- no acceptance-only bypass that hides a production integration gap.

When this roadmap and a GitHub issue diverge, the current issue wording and merged code are authoritative until the roadmap is refreshed again.
