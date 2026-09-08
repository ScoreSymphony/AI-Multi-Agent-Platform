# Issue #595 — integration handoff

Status: **implementation surfaces prepared on `feat/595-governed-learning-completion`; final composition and validation intentionally deferred to the unified active-branches integration branch.**

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

### Non-bypassable platform governance floor

`learning/governance.py` adds `LearningPlatformPolicy` and `GovernedObservedLearningService`.

The deployment-owned platform policy is independent of the Candidate-authored/versioned `LearningGatePlan`, so a Candidate cannot weaken the platform minimum. The prepared default policy:

- requires explicit versioned Evaluation suite references when Evaluation is required;
- requires explicit versioned Verification policy references when Verification is required;
- requires Approval for HIGH/CRITICAL risk;
- conservatively requires Approval for global/unscoped targets;
- disables automatic promotion platform-wide by default;
- only permits automatic promotion when both platform policy and Candidate policy explicitly allow it, and only for project-scoped STANDARD-risk changes.

Promotion still goes through the canonical owner service and #15 authorization after this floor is enforced.

### Control Plane

`learning/control_plane.py` prepares:

- `learning-candidates`;
- `learning-feedback`;
- feedback/proposal/evidence/accept/reject/supersede/promote commands;
- exact source/evidence/target projections;
- candidate revision history;
- Approval bindings;
- PromotionReceipt projection;
- post-promotion status derived from the runtime recorder when available.

`learning/runtime_control_plane.py` prepares the optional derived read collection:

- `learning-post-promotion-evaluations`.

### Source adapters

`learning/sources.py` prepares explicit bridges for:

- repeated Run failure patterns;
- repeated planner failure patterns;
- Research Evidence;
- operator proposals.

Repeated Run/planner patterns require at least two distinct exact source references, rather than trusting only a caller-supplied count. Research-derived Candidates bind the canonical Research item revision/digest, claim revision/digest, evidence digest and source-observation identity where available.

Feedback, Verification and Evaluation source creation remains in `LearningService`.

### Observability and post-promotion quality

`learning/runtime.py` prepares:

- `ObservedLearningService` for redacted log/timeline events;
- optional `EvaluationPostPromotionEvaluator`;
- derived post-promotion Evaluation records and recorder seam;
- idempotent suppression when the same promoted Candidate revision was already evaluated.

`learning/post_promotion_repository.py` adds `SQLitePostPromotionEvaluationRecorder`. The prepared Single-Node composition uses it by default, so post-promotion results survive restart instead of existing only in process memory.

Post-promotion evidence does not mutate prior candidate revisions or the promoted owner revision.

### Single-node composition

`learning/single_node.py` prepares `build_single_node_learning(...)` and `SingleNodeLearningComposition.register_control_plane(...)`.

The factory composes:

- `GovernedObservedLearningService`;
- `LearningPlatformPolicy` (default or explicitly supplied deployment policy);
- `SQLiteLearningRepository`;
- `SQLitePostPromotionEvaluationRecorder`;
- Agent/Skill/ModelRoutingProfile owner adapters;
- Evaluation/Verification quality gate;
- #15 AuthorizationGate;
- optional Research, Telemetry and post-promotion evaluator.

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

The CLI surface uses the canonical Feedback values (`correction`, `outcome_accepted`, `outcome_rejected`, `preference`, `rating`, `finding`, `comment`) and canonical risk values (`standard`, `elevated`, `high`, `critical`). Promotion keeps the existing CLI confirmation seam.

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
5. pass only manifest-advertised commands to `LearningDetailPage`;
6. expose the optional post-promotion collection only when advertised by the final manifest.

No `Shell.tsx` edit is made here to avoid conflicts with other active frontend branches.

## Explicitly deferred

The following are intentionally deferred until all active branches are unified:

- editing central `deployment/single_node.py`;
- editing final CLI dispatcher/composition;
- editing final frontend Shell/navigation;
- selecting the final target-aware `ConfigurationSnapshot` factory for post-promotion Evaluation;
- deciding whether the final deployment overrides `LearningPlatformPolicy` defaults;
- formatter, lint, typecheck, tests and CI;
- merge/readiness decision;
- closing issue #595.

## Integration order

Recommended order in the unified branch:

```text
reconcile canonical owner services
        -> compose Learning single-node service
        -> preserve/confirm LearningPlatformPolicy floor
        -> register Learning Control Plane
        -> register first-class Learning CLI
        -> mount Learning Web routes
        -> provide post-promotion snapshot factory if enabled
        -> resolve cross-branch API/type conflicts
        -> run one unified formatting/type/test/CI pass
        -> fix integration regressions
        -> merge and close #595 only when required checks are green
```

## Safety invariants to preserve during conflict resolution

Do not resolve integration conflicts by weakening these invariants:

- Learning Candidate is never authority;
- Candidate `LearningGatePlan` cannot weaken the deployment-owned platform governance floor;
- promotion creates a new revision through the canonical owner service;
- stale target binding fails closed;
- required Evaluation/Verification is exact-version-bound and fail-closed;
- high/critical and policy-selected global promotions retain exact-action #15 Approval;
- automatic promotion remains disabled by default and requires two independent policy opt-ins;
- repeated failure learning is backed by multiple concrete evidence references;
- historical source evidence and candidate revisions remain immutable;
- restart during owner promotion does not duplicate owner revisions;
- post-promotion Evaluation records are persistent/idempotent and remain derived evidence;
- no Learning path can grant permissions, install plugins or alter security policy;
- no private chain-of-thought is persisted;
- unsupported owner targets remain unsupported until a canonical owner adapter exists;
- no additional paid AI/API dependency is introduced.

## What “done” means after unification

#595 can be closed after the unified branch has wired the prepared composition points, resolved cross-branch conflicts, run the complete required validation once on the integrated tree, and all relevant required checks are green. This preparation branch must not be merged independently as evidence of completion.
