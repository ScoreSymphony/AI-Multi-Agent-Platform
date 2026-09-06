# ADR 0008: Defer an external durable workflow engine and add only missing platform durability

- **Status:** Accepted
- **Date:** 2026-09-05
- **Affected issue:** #21
- **Implementation follow-up:** #384
- **Operational evidence addendum:** [`0008-durable-workflow-operational-evidence.md`](0008-durable-workflow-operational-evidence.md)
- **Hard dependencies reviewed:** #1, #4, #5, #6, #9, #14, #18
- **Related decisions:** ADR 0005 (message transport is not canonical event history)
- **Decision outcome:** **Outcome 2 — Add minimal internal durability features; do not adopt Temporal or another external workflow engine now.**

## Context

Issue #21 asks whether the platform should adopt an external durable workflow engine, especially Temporal, and requires that decision to be made only after the platform-owned lifecycle, Forge reuse, distributed Worker recovery, and Automation semantics are concrete.

Those prerequisites are now complete. The decision can therefore be based on the platform that actually exists rather than on a hypothetical architecture.

The central invariant remains unchanged:

> The platform owns canonical Task, Plan, Step, Run, Worker Job, Event, Approval, Artifact and Result identity/state. A durable workflow implementation may help coordinate work, but it must never become a second canonical lifecycle system.

The current platform already has substantial durability that a generic workflow-engine comparison would otherwise count as missing:

- `PlatformKernel` owns canonical Task/Run state, idempotent commands, persisted Events and restart reconciliation (#6).
- Forge is an optional execution-only backend. Its durable dispatch identity and interruption behavior remain backend-private; Forge does not schedule canonical retries or own Task/Run state (`docs/FORGE_REUSE_AUDIT.md`, #9).
- The distributed runtime owns canonical Worker Jobs, heartbeat/liveness handling, reservations, dispatch idempotency, restart persistence, lost-worker reconciliation and fenced cross-Worker failover (`docs/DISTRIBUTED_RUNTIME.md`, #14).
- Automation owns persisted schedules, missed-run handling, durable TriggerDelivery retry state, deduplication, canonical Event ingestion and autonomous restart-safe wakeups (`docs/AUTOMATION.md`, #18).
- Message transport is explicitly at-least-once and separate from canonical Event history (ADR 0005).
- The single-node deployment already has platform-owned persistent stores and backup/restore semantics; adding another stateful service therefore has a real operational and recovery cost (`docs/DEPLOYMENT.md`, `docs/BACKUP_RESTORE.md`).

The remaining gap is narrower: the domain has canonical `Plan` and `Step` objects, dependency edges and Step lifecycle states, but the current platform does **not** yet provide a complete durable Plan/Step coordinator that persists and resumes multi-step dependency execution, long Step waits/signals, fan-out/fan-in barriers and Step-level retry deadlines as one coherent mechanism.

That gap is important, but it does not by itself prove that a separate workflow cluster is justified.

## Decision

### 1. Do not add Temporal, DBOS or another external durable workflow engine as a production dependency now

The platform will continue to use its existing canonical kernel, Automation runtime and distributed runtime for the durability responsibilities they already own.

Temporal remains a valid future adapter candidate, not a rejected technology. DBOS is retained as a serious lighter-weight alternative for the same future re-evaluation.

### 2. Add only the missing durable Plan/Step coordination semantics inside platform-owned contracts

A follow-up implementation should provide a narrow, replaceable durable coordination boundary and a reference implementation using platform-owned persistence. It should cover only capabilities that the current Task/Run/Automation/Worker layers do not already own:

1. **Durable Plan/Step execution state**
   - persist active Plan revision and Step runtime state;
   - preserve canonical `plan_*` / `step_*` IDs;
   - never encode backend workflow IDs as canonical identity.

2. **Dependency activation and fan-in/fan-out**
   - deterministically identify which Steps become `READY` after predecessor completion;
   - create canonical Runs idempotently for ready Steps;
   - persist barrier/fan-in progress so restart does not duplicate work.

3. **Durable Step waits**
   - represent a waiting Step using platform-owned wait metadata/records;
   - support at minimum a persisted deadline, canonical Approval resolution, and selected canonical Event/signal correlation;
   - wakeups must be idempotent and restart-safe.

4. **Step retry scheduling**
   - retry policy remains platform-owned;
   - each execution attempt remains a distinct canonical Run;
   - persisted retry deadlines resume after restart;
   - a coordinator retry must not be confused with Automation TriggerDelivery retry or executor-internal retry.

5. **Cancellation propagation**
   - canonical Task cancellation drives Plan/Step/Run/Worker Job cancellation according to existing ownership boundaries;
   - repeated cancellation remains safe.

6. **Coordinator reconciliation**
   - restart must recover waiting/ready/running Step coordination without blind redispatch;
   - when multiple Control-Plane processes are later active, one replaceable lease/claim mechanism must prevent two coordinators from advancing the same Step concurrently.

7. **Canonical event history**
   - significant coordinator transitions emit platform Events;
   - workflow progress must remain explainable from platform-owned state/history even when a future engine is used.

The implementation should reuse existing scheduling/retry/idempotency primitives when that reduces duplication, but it must not merge the Automation domain and Plan/Step execution into one hidden generic state machine. Their canonical responsibilities are different.

### 3. Preserve a future external-engine adapter boundary

A future external engine may implement the same narrow durable-coordination role, but the northbound contract must use only canonical platform IDs and backend-neutral wait/retry/cancellation descriptions.

The future boundary should follow these rules:

- input references canonical Task/Plan/Step/Run IDs;
- engine workflow/execution IDs are adapter references only;
- canonical lifecycle transitions are committed by platform services;
- engine-private history is diagnostic/coordination state, not canonical Event history;
- engine-native task queues do not replace canonical Worker Job state;
- engine-native retry attempts do not silently replace canonical Run attempts;
- frontend, CLI and public Control Plane APIs contain no engine-private types;
- a reference/simple path remains operable without the external engine.

This makes Temporal, DBOS or another engine replaceable later even if migration of *active* engine executions still requires an explicit procedure.

### 4. No Temporal prototype is required for this decision

A prototype would prove that Temporal can implement timers, retries, signals and restart-safe workflow execution; its documented behavior already establishes that. The decision-critical question is instead whether those capabilities justify a second durable subsystem **given the durability already implemented by this platform**.

Existing platform tests and documentation already cover the decision-critical behavior for short Tasks, kernel restart, Worker loss/reconciliation, duplicate delivery and scheduled Automation. The missing Plan/Step coordinator is visible directly in the platform boundary: canonical Plan/Step models and transitions exist, but there is no complete durable coordinator/repository layer for scenarios 2, 3 and 7 below.

A prototype becomes valuable only if a re-evaluation trigger is reached and two concrete implementations must be measured against the same canonical coordinator contract.

## Workload/scenario analysis

### Scenario 1 — Short task

**Workload:** One Task executes in seconds/minutes with one Run.

**Current platform:** Already handled by the kernel and execution boundary. Reference execution and Forge execution both preserve canonical Task/Run identity. Distributed placement may use the same Run/Worker Job contracts.

**Workflow-engine value:** Low. Temporal/DBOS would add coordination state and another failure/deployment boundary without solving an unowned lifecycle problem.

**Decision:** Keep on the normal platform path; an external workflow engine must never be mandatory for this scenario.

### Scenario 2 — Multi-step agent workflow

**Workload:** A Plan contains dependent Steps and may invoke several agents/tools.

**Current platform:** Canonical `Plan` and `Step` objects already exist, including dependency references and Step states. What is missing is a durable coordinator that advances the dependency graph and resumes it after restart.

**Workflow-engine value:** Real, but localized. Temporal and DBOS can provide durable step/workflow execution; however the platform still needs canonical Plan/Step ownership and mapping logic.

**Decision:** Implement the missing coordinator semantics internally first. External engines remain candidates behind that boundary if scale/complexity later justifies them.

### Scenario 3 — Long-running waiting task

**Workload:** A Step waits hours/days for human approval, an external event, a retry deadline or external job completion.

**Current platform:** Task/Step `WAITING`, canonical Approval, canonical Events, persisted Automation deadlines and Worker reconciliation provide the ingredients, but there is no single durable Step-wait record/coordinator that binds an individual waiting Step to its resume condition.

**Workflow-engine value:** High for timers/signals and long-lived waits.

**Decision:** Add persisted canonical wait conditions and wakeup reconciliation. Re-evaluate an engine if this layer begins recreating a broad workflow runtime rather than a small coordinator.

### Scenario 4 — Control-Plane process restart

**Workload:** The Control Plane restarts while work is active.

**Current platform:** Kernel recovery restores canonical lifecycle; Automation restores schedules/retries; the distributed runtime restores ownership/reservations conservatively and requires fresh liveness before reconciliation.

**Workflow-engine value:** Limited for already-owned Task/Run/Worker/Automation state.

**Decision:** No external engine required for this scenario. The new Plan/Step coordinator must follow the same persisted/reconcile-on-restart pattern.

### Scenario 5 — Worker/node loss

**Workload:** A Worker disappears after accepting a job.

**Current platform:** #14 already defines heartbeat expiry, `lost`, `cancel_pending`, idempotent result retrieval, persistent dispatch ownership and controlled cross-Worker failover only after a valid fence.

**Workflow-engine value:** Low for Worker ownership. Temporal task queues could provide another worker-delivery system, but that would duplicate canonical Worker Job scheduling/reconciliation.

**Decision:** Worker loss remains owned by #14. A workflow coordinator observes canonical Run/Worker outcomes; it does not replace the Worker protocol.

### Scenario 6 — Duplicate event/callback

**Workload:** Completion or trigger delivery arrives more than once.

**Current platform:** Kernel commands/events are idempotency-aware; #35 explicitly assumes at-least-once delivery; Worker Jobs are idempotent by canonical identity; Automation deduplicates TriggerDeliveries and retries the same persisted occurrence.

**Workflow-engine value:** Low. Engine-side duplicate handling cannot replace idempotency at platform/external side-effect boundaries.

**Decision:** Keep canonical idempotency platform-owned.

### Scenario 7 — Large fan-out/fan-in

**Workload:** One Plan creates many parallel Steps and waits for aggregate completion.

**Current platform:** Distributed Worker scheduling can execute many Jobs, and the domain can express Step dependencies, but there is no durable graph coordinator/barrier implementation yet.

**Workflow-engine value:** Medium to high as concurrency and graph size grow.

**Decision:** Implement deterministic persisted fan-out/fan-in in the internal coordinator first and benchmark it. Re-evaluate an external engine if coordinator throughput, history size or reconciliation complexity becomes a measured bottleneck.

### Scenario 8 — Scheduled automation

**Workload:** Recurring work must survive downtime and resume correctly.

**Current platform:** #18 already persists schedules, next evaluation, missed-run policy, delivery retries and autonomous wakeups and always creates work through canonical Task admission.

**Workflow-engine value:** Low and duplicative for the baseline.

**Decision:** Automation scheduling remains #18-owned. Do not move canonical Automation schedules into Temporal/DBOS merely to reuse their timer feature.

## Alternatives

### Option A — Platform-owned kernel only

Use the current Task/Run/Event kernel, Automation runtime and distributed runtime with no additional Plan/Step durability layer.

**Strengths**

- minimum operational footprint;
- clearest ownership;
- no new dependency or state store;
- existing Task/Run/Worker/Automation scenarios remain well covered.

**Weaknesses**

- does not fully solve durable dependent Step execution;
- lacks a first-class persisted Step wait/signal mechanism;
- fan-out/fan-in and Step retry wakeups would remain ad hoc.

**Conclusion:** Rejected as the final answer because it leaves a real end-state capability gap.

### Option B — Platform kernel + Forge lifecycle capabilities

Reuse more Forge behavior for workflow/recovery beyond the existing execution-only sidecar.

**Strengths**

- existing Forge adapter already supplies useful backend dispatch idempotency, cancellation and interruption evidence;
- no new third-party workflow product.

**Weaknesses**

- #9 deliberately rejected Forge Task/Project/Workflow lifecycle as canonical architecture;
- Forge recovery is intentionally scoped to execution evidence/identity, not canonical workflow scheduling;
- promoting it would reopen a boundary that #9 explicitly closed and would make one executor backend architecture-significant.

**Conclusion:** Rejected. Continue using Forge only for execution-owned behavior.

### Option C — Platform kernel + minimal internal durable Plan/Step coordinator

Add only the missing platform-owned Step graph/wait/retry/reconciliation semantics described in the Decision section.

**Strengths**

- fills the exact observed gap;
- preserves one canonical lifecycle/history;
- can reuse existing persistence, Events, Approval, Automation wakeup patterns and Worker reconciliation semantics;
- keeps the single-node profile small;
- provides the correct canonical boundary against which Temporal/DBOS can later be tested.

**Weaknesses**

- the project must implement and maintain coordinator logic;
- careless expansion could gradually recreate a general workflow engine;
- distributed active-active coordination eventually needs a lease/claim implementation.

**Conclusion:** **Selected.** Scope must remain intentionally narrow and re-evaluation triggers are mandatory.

### Option D — Temporal behind a durable-workflow adapter

Use Temporal only for selected Plan/Step coordination while retaining canonical platform state outside Temporal.

**Strengths**

- mature durable workflows, timers, signals, activity retries and replay-based recovery;
- strong multi-worker/distributed execution model;
- open-source Temporal Service is MIT licensed and self-hostable;
- local development server is easy to run.

**Weaknesses for this platform now**

- production self-hosting adds a Temporal Service with Frontend, History, Matching and Worker services plus persistence/visibility configuration, security, monitoring and upgrade responsibilities;
- the platform would back up and restore both its canonical stores and Temporal's workflow state;
- Temporal Workflow history/replay becomes operationally required for active workflows even when declared non-canonical;
- Workflow determinism/replay/versioning constraints add a second programming model;
- activity retries/timers/cancellation overlap conceptually with platform Run/Automation/Worker semantics and require careful non-duplication rules;
- Temporal unavailability would block progress of workflows delegated to it even though canonical platform state remains readable;
- active workflow migration to another engine cannot be assumed to be transparent.

**Conclusion:** Technically strong, but not justified by the current gap/footprint ratio. Defer.

### Option E — DBOS behind the same durable-workflow adapter

Use DBOS as a lighter library-based durable workflow implementation.

**Strengths**

- Python library model fits the current platform language well;
- durable workflows/steps, durable sleep and workflow recovery are built in;
- no separate orchestration server is required for the basic library architecture;
- open-source Python package is MIT licensed;
- current Python guidance supports a dependency-light local path and recommends Postgres for production.

**Weaknesses for this platform now**

- workflow/step checkpoints still create a second durable workflow state model alongside canonical Plan/Step state;
- production Postgres would add or accelerate a database dependency relative to the current SQLite-focused single-node baseline;
- distributed self-hosted recovery without DBOS Conductor requires explicit recovery coordination by the operator/application;
- using DBOS decorators/step checkpoints for platform business flow introduces library-specific execution semantics and active-workflow migration concerns;
- DBOS scheduling/queues would overlap with #18 Automation and #14 Worker scheduling if used beyond the narrow coordinator boundary.

**Conclusion:** A credible and lighter future candidate than Temporal for some deployments, but still not materially better than Option C for the current narrow gap. Defer.

## Qualitative evaluation matrix

Legend:

- **Strong** — naturally fits the criterion with low additional cost/risk.
- **Good** — fit is solid but needs some project-specific work.
- **Mixed** — meaningful benefit and meaningful cost/ambiguity coexist.
- **Weak** — criterion is poorly served for this platform at present.
- **Poor** — introduces material mismatch/operational burden for this criterion.

| Criterion | A — Kernel only | B — Kernel + Forge | C — Minimal internal | D — Temporal adapter | E — DBOS adapter |
| --- | --- | --- | --- | --- | --- |
| Architecture simplicity | **Strong** — no new layer | **Good** — Forge already exists, but workflow reuse blurs its execution boundary | **Good** — one narrow coordinator | **Weak** — another durable subsystem/service | **Mixed** — library is lighter, but still another workflow runtime |
| Canonical-state ownership clarity | **Strong** | **Strong** if Forge remains execution-only | **Strong** — platform owns all coordinator state | **Mixed** — requires strict split between canonical state and Temporal history | **Mixed** — requires strict split between canonical state and DBOS checkpoints |
| Recovery guarantees | **Good** for Task/Run/Worker/Automation; incomplete for Step graph | **Good** for backend execution; same Step gap | **Strong** for required platform scenarios once implemented | **Strong** | **Strong** |
| Retry support | **Good** for canonical Runs/Automation; incomplete Step retry deadline | **Good** plus backend retry hints | **Strong** — persisted policy + distinct Run attempts | **Strong**, but engine Activity retry must not hide canonical Run attempts | **Strong**, with the same canonical-attempt mapping requirement |
| Timer / scheduled-wait support | **Mixed** — Automation timers exist, Step waits do not | **Mixed** | **Strong** — add only Step wait deadlines | **Strong** | **Strong** |
| Long-running workflow support | **Mixed** | **Mixed** | **Strong** for canonical Plan/Step scope | **Strong** | **Strong** |
| Distributed execution support | **Good** — #14 already distributes Worker Jobs | **Good** | **Good/Strong** — coordinator can sit above #14 | **Strong**, but must not replace #14 | **Good**; distributed recovery adds coordination considerations |
| Idempotency support | **Strong** at canonical boundaries | **Strong** plus Forge request identity | **Strong** if wakeups/claims use canonical keys | **Strong**, but external side effects still need platform/application idempotency | **Strong**, same caveat |
| Cancellation semantics | **Good** across Task/Run/Worker; Step propagation incomplete | **Good** | **Strong** after explicit Step propagation | **Strong**, but canonical cancellation remains platform-owned | **Good/Strong**, with adapter translation |
| Operational complexity | **Strong** | **Good** — optional sidecar already understood | **Good** — reuses platform deployment | **Poor** — Temporal Service + datastore/visibility/security/monitoring/upgrades | **Good/Mixed** — library is light; production DB/recovery still adds operation |
| Resource footprint | **Strong** | **Good** | **Strong/Good** | **Poor** relative to the narrow missing feature set | **Good** relative to Temporal; still above pure internal state |
| Backup / restore complexity | **Good** — one platform recovery model | **Good** — Forge state remains subordinate | **Good** — coordinator can join platform backup contract | **Poor** — canonical stores plus Temporal persistence/history must be recovered coherently | **Mixed** — canonical stores plus DBOS workflow store/checkpoints |
| Developer complexity | **Strong** for existing paths | **Good** | **Mixed** — coordinator logic must be implemented carefully | **Weak** — deterministic Workflow/replay/versioning model plus adapter mapping | **Mixed** — simpler than Temporal but introduces DBOS workflow/step rules |
| Testability | **Strong** | **Strong** | **Strong** with deterministic reference coordinator | **Good** — SDK/test server available, but integration layer is larger | **Good** — library-level testing is straightforward |
| Observability | **Good** — canonical Events/Telemetry | **Good** | **Good/Strong** if coordinator emits canonical Events | **Strong** engine tooling, but creates a second diagnostic view | **Good/Strong** engine tooling, also a second diagnostic view |
| Single-node suitability | **Strong** | **Strong** when Forge is optional/local | **Strong** | **Weak/Mixed** — dev server is simple, production-grade self-hosting is a larger topology | **Good** — lightweight local mode; production guidance favors Postgres |
| Multi-node suitability | **Good** via #14 | **Good** | **Good**; requires coordinator lease/claim | **Strong** | **Good**; distributed recovery must be operated correctly |
| Replaceability / lock-in risk | **Strong** | **Strong** while Forge remains adapter-only | **Strong** — canonical implementation | **Weak/Mixed** — workflow code/history/replay complicate active migration | **Weak/Mixed** — decorated workflow/checkpoint semantics complicate active migration |
| Compatibility with Forge reuse | **Strong** | **Strongest** for execution behavior | **Strong** — coordinator remains above Executor | **Mixed** — risks overlapping retry/worker responsibilities | **Mixed** — same overlap risk |
| Compatibility with Automations (#18) | **Strong** | **Strong** | **Strong** — preserve separate domains and optionally share primitives | **Mixed** — Temporal timers/schedules could tempt duplicate ownership | **Mixed** — DBOS schedules/queues could tempt duplicate ownership |
| Migration complexity | **Strong** — none | **Good** — already integrated | **Good** — incremental canonical schema/service addition | **Poor** for active engine workflows; explicit drain/reconstruct procedure required | **Weak/Mixed** for active checkpointed workflows |
| Licensing / self-hosting fit | **Strong** | **Strong** under existing provenance | **Strong** | **Strong** — Temporal server is MIT/open source; paid Cloud is not required | **Strong** — DBOS Python package is MIT/open source; managed control plane must remain optional |

### Matrix conclusion

Option C is the only option that closes the concrete Plan/Step durability gap while preserving the platform's current single-source-of-truth and local-first operational shape. Temporal wins on general workflow-engine capability, but most of that capability either duplicates already-owned platform responsibilities or is not yet required. DBOS reduces infrastructure weight but does not remove the double-state and migration concerns.

## Lifecycle ownership table

The table below is normative for every option. An external engine is never allowed to silently become canonical simply because it stores durable state.

| Responsibility | A — Kernel only | B — Forge-assisted | C — Minimal internal | D — Temporal adapter | E — DBOS adapter |
| --- | --- | --- | --- | --- | --- |
| Canonical Task state | Platform kernel | Platform kernel | Platform kernel | Platform kernel | Platform kernel |
| Canonical Run state | Platform kernel | Platform kernel | Platform kernel | Platform kernel | Platform kernel |
| Canonical Plan/Step state | Platform | Platform | Platform coordinator/repository | Platform; Temporal stores private execution cursor/history only | Platform; DBOS stores private checkpoint/execution state only |
| Retry policy | Platform | Platform; Forge may report hints only | Platform | Platform policy; Temporal may perform engine-internal delivery/Activity retry only where mapped explicitly | Platform policy; DBOS internal retry/step mechanics must remain noncanonical |
| Canonical retry attempts | Distinct platform Runs | Distinct platform Runs | Distinct platform Runs | Distinct platform Runs; Temporal Activity attempts are diagnostic/private | Distinct platform Runs; DBOS step attempts are diagnostic/private |
| Timers / wait deadlines | Platform where implemented (#18) | Platform | Platform coordinator for Step waits; #18 for Automation | Platform stores canonical wait intent/deadline; Temporal may drive selected wakeup mechanism | Platform stores canonical wait intent/deadline; DBOS may drive selected wakeup mechanism |
| Cancellation authority | Platform | Platform -> Forge cancel bridge | Platform -> coordinator -> Run/Worker | Platform -> adapter -> Temporal | Platform -> adapter -> DBOS |
| Idempotency keys | Platform canonical keys | Platform + backend-private `request_ref` | Platform canonical keys | Platform keys; Temporal workflow/request IDs are adapter refs | Platform keys; DBOS workflow IDs are adapter refs |
| Canonical Event history | Platform Event store | Platform Event store | Platform Event store | Platform Event store; Temporal history is noncanonical | Platform Event store; DBOS workflow rows/history are noncanonical |
| Worker Job state | #14 platform distributed runtime | #14 platform distributed runtime | #14 platform distributed runtime | #14 platform distributed runtime; Temporal task queues cannot replace it | #14 platform distributed runtime; DBOS queues cannot replace it |
| Reconciliation authority | Platform | Platform; Forge supplies execution evidence | Platform coordinator + existing kernel/#14 reconciliation | Platform adapter reconciles engine observation into canonical state | Platform adapter reconciles engine observation into canonical state |
| Externally visible API | Platform Control Plane | Platform Control Plane | Platform Control Plane | Platform Control Plane only; no Temporal-private types | Platform Control Plane only; no DBOS-private types |

Any implementation that cannot maintain this table is incompatible with the platform architecture.

## Temporal-specific fit analysis

### Architecture fit

Temporal can be hidden behind a narrow adapter **only if** workflow definitions act as coordination drivers over canonical references rather than becoming the place where hidden platform business state lives.

A compliant future mapping would look approximately like:

```text
canonical Task / Plan / Step
        |
        v
Platform durable-coordinator port
        |
        v
Temporal adapter
        |
        +-- workflow_id = adapter reference
        +-- canonical IDs carried as workflow input/metadata
        +-- engine history = noncanonical coordination evidence
        |
        v
Platform services commit canonical Step/Run/Event transitions
```

Temporal must not create an alternative public Task model, expose its Workflow status directly as canonical Task status, or use Temporal Task Queues as the canonical Worker registry/scheduler.

### Durability and recovery

Temporal is materially stronger than the current platform's missing Step coordinator in these areas:

- durable execution history/replay;
- durable timers;
- signal/message-driven wakeups;
- Activity retry machinery;
- Worker/process failure recovery;
- long-lived Workflow execution.

Those are genuine advantages. The issue is not capability; it is whether the platform should accept the extra state machine and operations now.

For duplicate external effects, Temporal does not remove the need for idempotent platform/tool boundaries. A replay-safe Workflow may still call an external system through an Activity that itself needs an idempotency strategy.

### Temporal unavailability

If Temporal is selected in the future and becomes unavailable:

1. canonical Task/Plan/Step/Run state must remain queryable from platform persistence;
2. no new Temporal-backed workflow can start or advance while the service is unavailable;
3. non-Temporal/reference execution paths may continue where they do not depend on the unavailable coordinator;
4. the adapter reports a backend-unavailable/health state without rewriting canonical lifecycle to an invented terminal outcome;
5. after Temporal returns, the adapter reconciles the same canonical IDs rather than creating replacement Tasks/Runs blindly.

This preserves visibility but does not eliminate Temporal as a runtime dependency for workflows delegated to it.

### Operational footprint

The official self-hosted Temporal documentation describes a production Temporal Service rather than the dependency-free local development server. Production configuration includes Temporal Frontend, History, Matching and Worker services, persistence and Visibility configuration, plus security, monitoring and upgrade concerns. The local `temporal server start-dev` binary is explicitly positioned for development/testing.

The platform currently aims to make a modest self-hosted single-node deployment a valid production topology. Adding Temporal would therefore add:

- another always-on service plane;
- another durable datastore/schema lifecycle;
- another health/readiness and observability surface;
- additional TLS/auth/network configuration;
- additional backup/restore ordering and validation;
- additional upgrade/version-compatibility work;
- more CPU/RAM/storage than the current narrow missing Step coordinator needs.

This ADR intentionally does not invent numeric CPU/RAM estimates without a platform-specific benchmark. The decision is based on topology and ownership cost, not fabricated precision.

Temporal remains suitable for a future distributed profile if workloads prove that its guarantees justify that footprint.

### Developer complexity

Temporal's durable replay model is a second programming discipline in addition to the platform's canonical state machines. Workflow code must remain deterministic/replay-safe, side-effecting/non-deterministic work belongs in Activities or explicit durable mechanisms, and long-lived workflow definition changes require version/replay compatibility discipline.

That model is powerful, but adopting it now would force contributors to understand both:

- canonical platform lifecycle/event/reconciliation rules; and
- Temporal workflow/activity/history/replay rules.

For a narrow Plan/Step gap, that maintenance cost is not currently justified.

### Lock-in and replaceability

API-level lock-in can be constrained with an adapter. **Active-execution lock-in cannot be eliminated completely.** A running Temporal Workflow depends on its Temporal history and compatible workflow code. Replacing Temporal while such workflows are active therefore requires an explicit migration strategy, for example:

- drain/complete Temporal-backed workflows before cutover;
- cancel and reconstruct from canonical Plan/Step state where safe;
- or maintain the old engine until active histories expire.

The less hidden business state stored only in Workflow history, the easier reconstruction becomes. This is another reason the platform must retain canonical Plan/Step/wait state even if Temporal is later adopted.

## DBOS-specific fit analysis

DBOS is included because it materially changes the operational trade-off compared with Temporal: its durable workflow implementation is a library backed by database checkpoints rather than a separate orchestration service.

That makes it a credible candidate for this Python platform. Current DBOS documentation describes durable Workflows/Steps, durable sleep, restart recovery and queues, with production guidance centered on Postgres. The Python package is MIT licensed.

However, the architecture question remains the same:

- if DBOS workflow rows are allowed to become the only durable Step state, canonical ownership is split;
- if the platform also stores canonical Plan/Step/wait state, DBOS becomes a second state machine that must be reconciled;
- distributed self-hosted recovery still needs explicit coordination when not using the optional Conductor control plane;
- DBOS queues/schedules must not replace #14 Worker Jobs or #18 Automation.

DBOS is therefore **lighter than Temporal but not simpler than Option C for the capability currently missing**.

## Backup/restore implications

### Selected Option C

The durable coordinator should join the platform's existing backup/restore contract. A backup must capture coordinator state consistently with canonical Plan/Step/Run/Event state using the same release/schema manifest discipline already used by the platform.

### Future Temporal option

A production backup would need both:

- platform canonical persistence; and
- Temporal persistence/history/visibility state according to Temporal's supported backup/restore procedure.

Cross-store restore must avoid a state where canonical platform data says a Step is at one coordination point while Temporal history resumes another. The adapter would need a post-restore reconciliation procedure and explicit recovery invariants.

### Future DBOS option

The DBOS system database/checkpoint state would likewise join the restore set. It is operationally lighter than a Temporal cluster but still requires cross-store reconciliation unless the platform deliberately co-locates and transactionally couples canonical and DBOS state, which would itself increase backend coupling.

## Why Forge does not remove the remaining gap

`docs/FORGE_REUSE_AUDIT.md` intentionally keeps Forge below `LifecycleBackend` and records that:

- canonical restart reconciliation is platform-owned;
- Forge dispatch identity is backend-private;
- retry policy remains above the executor;
- Forge Task/Project/Workflow lifecycle was rejected;
- Forge is optional and removable.

Using Forge to own the new Plan/Step coordinator would reverse those decisions and couple orchestration durability to one executor backend. Forge remains useful evidence for execution recovery, not a substitute workflow engine.

## Why Automation does not become the workflow engine

#18 already solves durable schedule/event **admission**:

```text
Trigger -> Automation evaluation -> canonical Task creation -> normal lifecycle
```

A Plan/Step coordinator solves durable **in-task progression** after a Task exists. Reusing common primitives such as persisted deadlines, retry calculations or wakeup polling can be sensible, but Automation must not start executing Steps directly and the coordinator must not reinterpret recurring Automations as child workflows.

Keeping these responsibilities distinct prevents another accidental lifecycle system.

## Re-evaluation triggers

Temporal/DBOS/another engine must be re-evaluated if one or more of the following become true:

1. **Internal coordinator scope starts expanding into a general workflow engine.**
   Examples: child-workflow hierarchies, many classes of durable signals, workflow-level version/replay machinery, complex queryable workflow history or multiple independent timer/queue subsystems are being recreated internally.

2. **Measured fan-out/fan-in scale exceeds the reference coordinator's design.**
   A benchmark with representative agent workloads shows the coordinator or its persistence/reconciliation loop is a material throughput/latency bottleneck.

3. **Long-running workflow operational incidents remain hard after the minimal coordinator exists.**
   Repeated duplicate wakeups, stuck waits, unsafe recovery or difficult operator repair indicate that a mature external engine could materially reduce risk.

4. **Active-active / cross-region durable workflow coordination becomes a product requirement.**
   The platform needs durable workflow failover semantics beyond the existing Worker-level distributed model.

5. **Workflow replay/version migration becomes a first-class product requirement.**
   Operators need to replay deterministic orchestration logic, inspect long histories or migrate in-flight workflow code in ways the canonical Event + coordinator model cannot reasonably support.

6. **A concrete external-engine prototype demonstrates a material advantage.**
   Temporal, DBOS or another engine passes the same canonical adapter contract while materially reducing implementation/operations burden or improving recovery/scale in representative workloads.

7. **The storage/deployment baseline changes enough to alter the cost equation.**
   For example, if a shared production Postgres dependency is already mandatory across deployments, DBOS's incremental operational cost may be lower than it is for the current SQLite-friendly single-node profile. This alone is not sufficient; ownership and migration concerns must still be satisfied.

When a trigger is reached, the re-evaluation must compare at least the current internal coordinator, Temporal and the strongest lightweight candidate at that time. It must use the same workload suite and ownership table rather than restarting from generic product marketing claims.

## Required guardrails for any future external-engine adoption

These invariants are mandatory and repeat the #21 guardrails in implementation terms:

1. Canonical Task/Plan/Step/Run IDs remain platform-owned.
2. Frontend, CLI and public APIs expose no engine-private types.
3. Engine workflow IDs are external adapter references only.
4. Platform persistence/Event history remains the authoritative externally visible lifecycle.
5. No second canonical Task/Run lifecycle is created.
6. Core unit tests and the simple/reference execution path do not require the engine.
7. Worker Job scheduling remains #14-owned unless a separate ADR explicitly replaces that architecture.
8. Automation schedule/TriggerDelivery ownership remains #18-owned unless a separate ADR explicitly replaces it.
9. Engine-internal retries do not silently replace canonical Run attempts.
10. The deployment remains self-hostable without paid managed services.
11. Backup/restore and upgrade procedures cover cross-store reconciliation explicitly.
12. Active-workflow migration/retirement is documented before the adapter is production-enabled.

## Consequences

### Positive

- The platform avoids adding a large runtime before it has a demonstrated need.
- Single-node/self-hosted operation remains small and paid-service-free.
- Existing Kernel, Forge, Worker and Automation durability continues to be used rather than duplicated.
- The actual missing feature — durable Plan/Step coordination — becomes explicit and testable.
- A future Temporal/DBOS comparison will happen against a stable canonical coordinator contract instead of allowing an engine to define the domain model.
- The project avoids prematurely making workflow-engine history part of backup, recovery and migration correctness.

### Costs

- The project must implement a limited durable coordinator itself.
- Scope discipline is required so that "minimal internal" does not grow without re-evaluation.
- Some features Temporal/DBOS would provide immediately (signals, durable timers, replay tooling) will initially be narrower or absent.
- A later engine adoption may require replacing the reference coordinator implementation and migrating active coordination state deliberately.

## Rejected alternatives and rationale

- **Kernel only forever:** rejected because durable multi-step wait/dependency coordination is a real platform gap.
- **Promote Forge workflow lifecycle:** rejected because it violates the execution-only adapter boundary established by #9.
- **Adopt Temporal now:** rejected/deferred because its broad guarantees do not currently justify the extra service, datastore, recovery, developer and migration surface.
- **Adopt DBOS now:** rejected/deferred because its lighter operations do not remove the second-state/migration problem and Option C is still smaller for the current gap.
- **Make Automation the generic workflow system:** rejected because trigger-to-Task admission and in-Task Step progression are distinct canonical responsibilities.

## Evidence and references

### Platform evidence

- `src/ai_multi_agent_platform/domain/models.py` — canonical Plan/Step/Run models and lifecycle-capable Step state.
- `docs/FORGE_REUSE_AUDIT.md` — Forge execution/recovery ownership and rejected legacy workflow lifecycle.
- `docs/DISTRIBUTED_RUNTIME.md` — Worker Job ownership, leases, restart persistence, reconciliation and fencing.
- `docs/AUTOMATION.md` — persisted schedules, TriggerDelivery retry/dedupe and autonomous restart-safe runtime.
- `docs/DEPLOYMENT.md` — production-shaped single-node topology and platform stores.
- `docs/BACKUP_RESTORE.md` — platform backup/restore ownership.
- `docs/adr/0005-separate-message-transport-from-canonical-event-history.md` — at-least-once message delivery remains separate from canonical Event history.

### External-engine evidence reviewed on 2026-09-05

Temporal:

- Self-hosted service guide: <https://github.com/temporalio/documentation/blob/main/docs/production-deployment/self-hosted-guide/index.mdx>
- Temporal architecture / Service and history model: <https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/architecture/how-temporal-works.mdx>
- Temporal Service configuration components: <https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/temporal-service/temporal-service-configuration.mdx>
- Temporal server license (MIT): <https://github.com/temporalio/temporal/blob/main/LICENSE>

DBOS:

- Why DBOS / durable-workflow model: <https://docs.dbos.dev/why-dbos>
- Architecture: <https://docs.dbos.dev/architecture>
- Python durable Workflows and durable sleep: <https://docs.dbos.dev/python/tutorials/workflow-tutorial>
- Production workflow recovery: <https://docs.dbos.dev/production/workflow-recovery>
- Python package metadata/license (MIT): <https://github.com/dbos-inc/dbos-transact-py/blob/main/pyproject.toml>

## Acceptance criteria reconciliation

- [x] Concrete platform workload scenarios are documented.
- [x] Platform-kernel-only, Forge-assisted, minimal-internal and Temporal options are compared.
- [x] A serious lighter external alternative (DBOS) is included without turning the ADR into an unbounded survey.
- [x] Lifecycle ownership is explicit for every serious option.
- [x] Recovery, retries, timers, cancellation, idempotency and long-running work are evaluated.
- [x] Operational/resource footprint is evaluated for self-hosted single-node and distributed use.
- [x] Temporal lock-in and active-workflow migration implications are documented.
- [x] No production workflow-engine dependency is introduced by this issue.
- [x] The ADR explains why an isolated prototype is not decision-critical now and when one becomes required.
- [x] The final recommendation is explicit: Outcome 2.
- [x] Concrete re-evaluation triggers are documented.

## Status transition

This ADR is **Accepted** for #21. The platform will implement the narrow platform-owned durable Plan/Step coordinator in #384 and will re-evaluate Temporal, DBOS or another external engine only when the documented triggers are reached.
