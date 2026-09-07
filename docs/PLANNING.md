# Autonomous planning and bounded replanning

Issue #439 adds a platform-owned planning layer between canonical Task intent and the existing durable Plan/Step runtime.

The ownership direction is:

```text
Goal / Task intent
    -> Planning proposal
    -> validation / preview / approval
    -> canonical Plan + Steps
    -> durable #384 coordinator
    -> canonical Runs
    -> executor / #14 Worker placement
    -> Results / Artifacts
    -> #86 Verification
```

Planning owns **proposal generation, validation, revision intent and provenance**. It does not own Step execution, retries, waits, fan-out/fan-in state, capability invocation, model-provider execution, Worker scheduling or verification authority.

## Canonical boundaries

The planner consumes platform-owned references only:

- canonical Task identity and revision;
- exact Agent or Agent-Team revisions from #33, or a role requirement resolvable against that inventory;
- canonical Capability IDs and requirements from #12;
- canonical model configuration requirements from #10;
- Workspace and Project scope references;
- prior Plan/Run/Result/Artifact evidence;
- policy and verification references.

Provider-native model names, endpoint identities, host paths, Node/Worker IDs and private tool/provider identities are deliberately absent from planning contracts. A proposal containing provider-private runtime identity in Step metadata fails validation.

## Proposal lifecycle

`PlanningService.propose(...)` creates an immutable `PlanProposal` and a durable `ProposalRecord`.

A proposal records:

- Task ID and exact Task revision used as input;
- proposed Plan revision and prior canonical Plan ID when replanning;
- trigger and reason;
- DAG Steps and dependencies;
- Agent/Team assignment intent;
- capability and model requirements;
- Workspace/input/output/evidence references;
- assumptions and constraints;
- completed prior Step IDs explicitly reused;
- planner identity/version;
- canonical model configuration ID when a model-backed planner was used;
- a stable proposal digest.

Proposal state is distinct from canonical Plan state. Previewing or validating a proposal does not create Runs or start execution.

The reference JSON repository persists proposal state atomically and restores `VALIDATED`, `AWAITING_APPROVAL`, `ACTIVATING`, `ACTIVATED`, `INVALID` and rejected history across process restart. Duplicate idempotency keys and duplicate equivalent trigger fingerprints resolve to the existing proposal rather than creating competing active proposals.

## Validation

Before activation the platform validates at least:

1. non-empty Step graph and configured Step limit;
2. unique Step keys;
3. dependency references;
4. acyclic DAG structure;
5. optional parallelism limit;
6. exact Agent/Team revision or resolvable role requirement;
7. enabled Team members;
8. required Agent capabilities represented by the Step;
9. capability availability, exact version and required features;
10. capability permission requirements supplied by the trusted caller boundary;
11. Agent allow/deny capability policy;
12. model satisfiability using canonical model metadata only;
13. completed-work reuse references only completed prior Steps;
14. absence of provider-private runtime identity.

Invalid proposals remain visible as proposal history but cannot activate.

## Activation and #384 handoff

Activation is an explicit command. `PlanningOrchestratorAdapter` temporarily exposes exactly one `ACTIVATING` proposal through the existing `Orchestrator.plan(...)` seam so `PlatformKernel.plan_task(...)` remains the sole allocator of canonical Plan and Step IDs.

After the canonical `plan.created` event exists, `PlanningService` reconstructs the exact canonical Plan/Step graph from that event and calls `DurablePlanStepCoordinator.register_plan(...)`.

This separation is intentional:

- the planner never creates a Run;
- the planner never starts a Step;
- the planner never dispatches a Worker;
- the planner never invokes a capability;
- #384 determines runnable Steps and dependency progression;
- #14 remains responsible for Worker/Node placement when distributed execution is enabled.

The handoff is restart-safe. If the process stops after `plan.created` but before the proposal record is marked `ACTIVATED`, a retry discovers the exact proposal provenance in canonical event history, replays the idempotent #384 registration and then repairs proposal state.

## Replanning

Replanning is triggered by explicit canonical reasons such as:

- terminal failure or retry exhaustion;
- verification changes-required/failure/inconclusive outcomes;
- unavailable Agent, Capability or model configuration;
- Task constraint changes;
- invalidated assumptions or feasibility blockers;
- explicit authorized manual request.

A non-initial proposal requires an existing canonical Plan and a non-empty reason. Equivalent duplicate triggers are idempotent. `ReplanPolicy.max_replans` provides a hard bounded budget so autonomous replanning cannot loop indefinitely.

A replacement proposal may refer to previous Steps through `reuse_step_ids`, but only Steps whose latest canonical Run succeeded are eligible. Prior running work cannot be silently detached: replacement activation is rejected while predecessor Steps remain active. The previous Plan and proposal history remain immutable provenance.

## Authorization and approvals

Planning does not grant permissions. Control Plane clients cannot claim `granted_permissions` through the planning command payload.

Sensitive capability requirements mark a proposal as approval-gated. Activation fails closed if an approval authority is required but unavailable. When #15 is configured, activation binds authorization/approval to the exact proposal digest and Plan-revision action. Capability execution later still passes through the normal #12/#15 invocation gates; approving a Plan does not bypass capability authorization.

## Planner implementations

Two reference planners are available:

- `DeterministicReferencePlanner`: no LLM or paid service required; useful for local operation and contract tests.
- `ModelBackedPlanner`: routes through the canonical #10 `ModelRouter` and `ModelRegistry`, then records only the selected canonical model configuration ID in proposal provenance.

`PlanningOrchestratorAdapter` is not a planner implementation authority of its own. It is only the activation bridge from a validated proposal into the existing kernel planning seam and can retain an ordinary fallback Orchestrator for pre-#439 callers.

## Control Plane

The planning extension exposes the `planning-proposals` resource projection and the commands:

- `planning.propose` — create/preview a validated proposal for a Task;
- `planning.activate` — activate one exact proposal, with optional canonical approval reference;
- `planning.reject` — reject a proposal without mutating canonical Plan/Run state.

Proposal resources expose status, digest, planner provenance, assumptions, constraints, Step graph, assignment/capability/model requirements, validation findings, approval reference and activation Plan ID.

## Standard single-node composition

The public `build_single_node_deployment(...)` path composes #439 as a normal durable platform service:

- `JsonPlanningRepository` persists proposal/replanning state in `db/planning.json`;
- a dedicated `PlatformKernel` shares the canonical kernel event store solely for Task/Plan mutation and history;
- that planning kernel uses `PlanningOnlyLifecycleBackend`, which rejects `start`, `get` and `cancel` execution operations with `FORBIDDEN`;
- activated Plan/Step graphs are handed to the already composed `DurablePlanStepCoordinator` rather than being executed by the planning kernel;
- `planning-proposals` and the three planning commands are registered on the authenticated Control Plane;
- safe planning transition evidence is projected into the normal observability timeline.

The separate planning kernel is an enforcement boundary, not a second Task/Run authority: it uses the same canonical event repository, but its lifecycle dependency makes direct Run execution impossible. The normal deployment kernel remains the execution path used by #384 and, when enabled, #14 distributed scheduling.

## Observability and evaluation

Planning emits safe proposal/validation/activation/handoff events through its event sink. Canonical `plan.created` history additionally carries the `platform-planning` adapter namespace with proposal digest, planner/version, canonical model configuration, trigger, constraints, evidence and reused/superseded Plan references.

Issue-specific tests exercise deterministic and model-backed planning, DAG validation, satisfiability failures, stale and duplicate proposals, approval fail-closed behavior, restart recovery, #384 handoff, completed-work reuse, bounded replanning and the standard single-node composition. These behaviors are suitable as deterministic #19 evaluation subjects without making the evaluator or planner a lifecycle authority.
