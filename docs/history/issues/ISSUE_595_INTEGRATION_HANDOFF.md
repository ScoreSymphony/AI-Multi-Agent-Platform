# Issue #595 — integration handoff

Status: **prepared on `feat/595-governed-learning-completion`; validation intentionally deferred to the unified active-branches integration branch.**

This handoff exists so the later integration branch can reconcile #595 without reconstructing design decisions from individual commits.

## Already available in main

The canonical Learning core arrived through the earlier integration of #595 groundwork:

- `learning/models.py` — immutable candidate/feedback contracts;
- `learning/repository.py` — in-memory and SQLite persistence/dedupe;
- `learning/service.py` — quality gate, lifecycle and promotion coordinator;
- `learning/promotion.py` — Agent, Skill and ModelRoutingProfile owner adapters.

## Prepared by this branch

### Acceptance coverage

`tests/test_issue_595_governed_learning.py` contains the issue-required scenarios:

- user correction -> candidate;
- Verification finding -> candidate;
- Evaluation regression -> candidate;
- duplicate candidate dedupe/link;
- Agent revision proposal/evaluation/promotion;
- Skill revision proposal/evaluation/promotion;
- routing-profile proposal;
- stale target revision rejection;
- unauthorized promotion denial;
- Approval-gated promotion;
- failed Evaluation blocks promotion;
- restart during promotion does not duplicate owner revision;
- historical source evidence remains unchanged.

These tests are preparation only on this branch. Repository-wide execution is deferred by project decision to the unified integration branch.

### Control Plane

`learning/control_plane.py` prepares:

- `learning-candidates`;
- `learning-feedback`;
- feedback/proposal/evidence/accept/reject/supersede/promote commands;
- exact source/evidence/target projections;
- candidate revision history;
- Approval bindings;
- PromotionReceipt projection.

`learning/runtime_control_plane.py` prepares the optional derived read collection:

- `learning-post-promotion-evaluations`.

### Source adapters

`learning/sources.py` prepares explicit bridges for:

- repeated Run failure patterns;
- repeated planner failure patterns;
- Research Evidence;
- operator proposals.

Feedback, Verification and Evaluation source creation remains in `LearningService`.

### Observability and post-promotion quality

`learning/runtime.py` prepares:

- `ObservedLearningService` for redacted log/timeline events;
- optional `EvaluationPostPromotionEvaluator`;
- derived post-promotion Evaluation records and recorder seam.

Post-promotion evidence does not mutate prior candidate revisions or the promoted owner revision.

### Single-node composition

`learning/single_node.py` prepares `build_single_node_learning(...)` and `SingleNodeLearningComposition.register_control_plane(...)`.

It deliberately does **not** edit `deployment/single_node.py` on this branch. The unified branch should compose it once after resolving the final shared instances of:

- AgentService;
- SkillService;
- ModelRoutingProfileService;
- EvaluationService;
- VerificationService;
- AuthorizationGate;
- optional ResearchService and Telemetry.

### CLI

`cli/learning.py` prepares first-class API-only commands. Generic extension mutation remains disabled.

Unified branch wiring:

1. import `add_learning_parser` / `execute_learning` into the final CLI composition;
2. register `platform learning` once;
3. dispatch it through the existing authenticated `ControlPlaneClient` and confirmation helper.

### Web

Prepared additive files:

- `frontend/src/api/learning.ts`;
- `frontend/src/pages/LearningPage.tsx`;
- `frontend/src/app/learningManifest.ts`.

Unified branch wiring:

1. construct `LearningClient` from the final Control Plane base URL;
2. derive capability state through `learningManifestCapabilities(...)`;
3. add `/learning` and `/learning/:learningCandidateId` routes;
4. add the final navigation entry;
5. pass only manifest-advertised commands to `LearningDetailPage`.

No `Shell.tsx` edit is made here to avoid conflicts with other active frontend branches.

## Explicitly deferred

The following are intentionally deferred until all active branches are unified:

- editing central `deployment/single_node.py`;
- editing final CLI dispatcher/composition;
- editing final frontend Shell/navigation;
- selecting the final target-aware `ConfigurationSnapshot` factory for post-promotion Evaluation;
- formatter, lint, typecheck, tests and CI;
- merge/readiness decision;
- closing issue #595.

## Integration order

Recommended order in the unified branch:

```text
reconcile canonical owner services
        -> compose Learning single-node service
        -> register Learning Control Plane
        -> register first-class Learning CLI
        -> mount Learning Web routes
        -> resolve cross-branch API/type conflicts
        -> run one unified formatting/type/test/CI pass
        -> fix integration regressions
        -> merge and close #595 only when required checks are green
```

## Safety invariants to preserve during conflict resolution

Do not resolve integration conflicts by weakening these invariants:

- Learning Candidate is never authority;
- promotion creates a new revision through the canonical owner service;
- stale target binding fails closed;
- required Evaluation/Verification is version-bound and fail-closed;
- high-risk promotion retains exact-action #15 Approval;
- historical source evidence and candidate revisions remain immutable;
- no Learning path can grant permissions, install plugins or alter security policy;
- no private chain-of-thought is persisted;
- unsupported owner targets remain unsupported until a canonical owner adapter exists;
- no additional paid AI/API dependency is introduced.
