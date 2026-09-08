# Governed Feedback and Learning Promotion

Issue #595 adds a governed improvement pipeline around existing canonical owner domains. Learning Candidates are proposals plus evidence. They never become authority merely because they were generated automatically or because a model/user expressed a preference.

## Canonical flow

```text
Outcome / correction / finding
        |
        v
Learning Candidate
        |
        v
Evaluation / Verification evidence
        |
        v
Accepted proposal
        |
        +--> deployment-owned governance floor
        |
        +--> #15 Approval when required
        |
        v
Owning canonical service creates a NEW revision
        |
        v
Promotion receipt + optional post-promotion Evaluation
```

Historical Runs, Evaluation results, Verification results, feedback records and prior Learning Candidate revisions remain immutable.

## Authority boundaries

Learning does not replace the owner of the resource being improved.

| Target | Canonical promotion owner |
| --- | --- |
| AgentDefinition | AgentService |
| Skill | SkillService |
| ModelRoutingProfile | ModelRoutingProfileService |
| Template/config | owner adapter required before support |
| Memory / Knowledge | owner adapter required before support |
| planner policy | owner adapter required before support |
| Verification/Evaluation policy | owner adapter required before support |
| docs/ADR/Decision proposal | owner adapter required before support |

The initial promotion registry implements Agent, Skill and ModelRoutingProfile only. Unsupported owner domains fail closed through `PromotionRegistry` instead of using a generic write path.

A Learning Candidate cannot grant permissions, install plugins, change security policy, bypass Verification/Evaluation or directly modify an owner repository.

## Candidate contract

`LearningCandidate` records:

- stable candidate ID and append-only candidate revision;
- source type and exact source/evidence references;
- problem statement and expected benefit/hypothesis;
- exact target resource type, ID and base revision;
- improvement type and structured proposed change or artifact reference;
- explicit risk classification;
- versioned `LearningGatePlan`;
- Evaluation run IDs and Verification IDs;
- lifecycle status (`proposed`, `evaluating`, `accepted`, `rejected`, `superseded`, `promoted`);
- creator provenance, timestamps and content digest;
- optional immutable `PromotionReceipt` after successful promotion.

Duplicate proposals use a deterministic dedupe key. Additional equivalent findings are linked as source/evidence references by creating a new candidate revision rather than mutating history.

## Sources

The current source bridges support:

- explicit user feedback/correction;
- Verification failures, `needs_changes` and inconclusive findings;
- Evaluation regressions;
- repeated Run failure patterns;
- repeated planner failure patterns;
- canonical Research Evidence;
- explicit operator proposals.

Free-form conversation text is not learned automatically. It may be stored only as supporting feedback/evidence when an explicit feedback operation creates a canonical record.

`LearningSourceBridge` provides explicit adapters for Run/planner patterns, Research Evidence and operator proposals. Repeated Run/planner patterns require at least two distinct concrete source references rather than trusting only a caller-provided counter. Research-derived candidates preserve exact item/claim/evidence/source-observation bindings, including revision/digest data where available. `LearningService` owns the direct Feedback, Verification and Evaluation bridges.

## Candidate quality gate

`LearningQualityGate` reads canonical #19 Evaluation and #86 Verification evidence. It does not create substitute quality results.

Evaluation acceptance fails closed when:

- Evaluation is required but unavailable;
- no required Evaluation run is bound;
- a run is incomplete;
- a run uses a suite outside the candidate's versioned allow-list;
- a result is not passed;
- a regression exists while `require_regression_free` is enabled.

Verification acceptance follows the same pattern for configured Verification policies and requires a canonical PASS result.

## Platform governance floor

`LearningPlatformPolicy` is independent from the Candidate's `LearningGatePlan`. It is owned by the deployment and exists so a Candidate cannot weaken the platform minimum by proposing a permissive gate plan.

The prepared default policy:

- requires explicit versioned Evaluation suite references when Evaluation is enabled;
- requires explicit versioned Verification policy references when Verification is enabled;
- requires Approval for HIGH and CRITICAL risk;
- conservatively requires Approval for global/unscoped targets;
- disables automatic promotion at platform level by default.

`GovernedObservedLearningService` enforces this floor during candidate creation, acceptance and promotion. Automatic promotion requires both the platform policy and Candidate policy to opt in and is restricted to project-scoped STANDARD-risk candidates by the prepared default implementation.

## Promotion

Promotion is allowed only for an accepted candidate. Before invoking an owner adapter the governed service:

1. validates the deployment-owned platform governance floor;
2. re-runs the candidate quality gate;
3. resolves the explicit owner adapter;
4. constructs a canonical #15 `ProposedAction` bound to candidate ID, candidate revision, candidate digest and exact target revision;
5. requires exact-action Approval whenever either the platform policy or Candidate policy requires it;
6. enforces authorization;
7. asks the owner service to create the next canonical revision.

Owner adapters reject stale target revisions. If a process stops after the owner revision is created but before the Learning candidate is marked promoted, the adapter recognizes its own exact candidate provenance and returns an idempotent recovery receipt instead of creating another owner revision.

## Audit and telemetry

`ObservedLearningService` emits redacted structured log and timeline events for:

- explicit feedback recording;
- candidate creation/dedup linking;
- gate-evidence binding;
- accept/reject/supersede decisions;
- owner promotion;
- optional post-promotion Evaluation completion/failure.

Telemetry carries IDs, revisions, digests, target identity, risk and gate-policy references. It does not store private chain-of-thought or proposed secret values.

## Optional post-promotion Evaluation

`EvaluationPostPromotionEvaluator` can run configured #19 suites against a snapshot representing the newly promoted owner revision. The last matching pre-promotion Evaluation run is used as a baseline when available.

Post-promotion results are derived evidence. They do not rewrite the PromotionReceipt or the owner revision. `learning-post-promotion-evaluations` exposes the recorded outcome and canonical Evaluation run IDs. A regression can therefore be surfaced and acted upon by a new Learning Candidate or rollback workflow without rewriting history.

The runtime checks whether the same Candidate revision/promotion target revision already has a post-promotion record before scheduling another evaluation. `SQLitePostPromotionEvaluationRecorder` persists these derived records across restart and is the prepared Single-Node default.

The integration must provide the `ConfigurationSnapshot` factory because target-specific snapshot construction belongs to the owning deployment/evaluation composition. If no post-promotion evaluator is configured, no synthetic PASS result is created.

## Persistence and recovery

`SQLiteLearningRepository` persists:

- immutable feedback records;
- append-only Learning Candidate revisions;
- stable dedupe bindings.

`SQLitePostPromotionEvaluationRecorder` separately persists derived post-promotion evaluation records and enforces stable record identity plus one record for the same candidate revision/target revision pair.

The owner-domain provenance stored by Agent/Skill/Routing adapters makes promotion restart-safe even if the Learning process stops between owner mutation and candidate receipt persistence.

## Control Plane

`register_learning_control_plane(...)` adds:

- `learning-candidates`;
- `learning-feedback`.

Candidate projections include source/evidence references, exact target binding, proposed change, Evaluation/Verification IDs, candidate revision history, related Approval metadata, PromotionReceipt and the actual post-promotion status when the runtime recorder is available.

Commands:

```text
learning.feedback.create
learning.propose
learning.propose-from-feedback
learning.evidence
learning.accept
learning.reject
learning.supersede
learning.promote
```

`register_learning_runtime_control_plane(...)` optionally adds the read-only derived collection:

```text
learning-post-promotion-evaluations
```

All mutation commands still pass through the normal Control Plane authorization bridge. `learning.promote` then performs the second, exact owner-target authorization/Approval check inside Learning itself.

## CLI

The generic extension CLI remains deliberately read-only. It can inspect registered Learning collections and discover advertised commands:

```bash
platform extension list learning-candidates
platform extension show learning-candidates <learning_candidate_id>
platform extension list learning-feedback
platform extension list learning-post-promotion-evaluations
platform extension commands
```

Mutations use the prepared first-class `platform learning` adapter in `src/ai_multi_agent_platform/cli/learning.py`, not a generic extension executor. The adapter exposes explicit feedback, proposal, evidence, accept/reject/supersede and promotion commands, uses the canonical Feedback/Risk enum values and preserves the CLI's normal confirmation semantics for promotion. The unified integration branch must register and dispatch that module in the final CLI composition. See `docs/cli/CLI_LEARNING.md` for concrete command examples.

## Web preparation

The branch prepares these additive frontend modules:

- `frontend/src/api/learning.ts` — typed read/write Control Plane client;
- `frontend/src/pages/LearningPage.tsx` — queue, detail, evidence/history and decision surfaces;
- `frontend/src/app/learningManifest.ts` — manifest-aware capability detection.

The later integration branch should mount:

```text
/learning
/learning/:learningCandidateId
```

and pass the manifest-advertised Learning commands to `LearningDetailPage`. Missing commands remain disabled; the browser never invents a direct service fallback.

## Single-node integration seam

`build_single_node_learning(...)` prepares the production-shaped composition using existing canonical services:

- AgentService;
- SkillService (existing service can be supplied; otherwise a durable `skills.json` owner store is created);
- ModelRoutingProfileService;
- EvaluationService;
- VerificationService;
- AuthorizationGate / Approval service;
- `LearningPlatformPolicy` (default or deployment override);
- SQLite Learning Candidate/feedback persistence;
- SQLite post-promotion evaluation persistence;
- optional Telemetry;
- optional ResearchService;
- optional PostPromotionEvaluator.

`SingleNodeLearningComposition.register_control_plane(...)` registers only missing additive Skill/Learning collections. This is intentionally separated from `deployment/single_node.py` on the issue branch so the later all-active-branches integration can wire it once after reconciling other composition changes.

Recommended integration shape:

```python
learning = build_single_node_learning(
    database_dir=database_dir,
    agents=agents,
    skills=skills,
    routing_profiles=routing_profiles,
    evaluation=evaluation_composition.service,
    verification=verification,
    approval_gate=approval_gate,
    telemetry=telemetry,
    research=research,
)
learning.register_control_plane(control_plane)
```

The shared `SingleNodeDeployment` can then expose `skills` and `learning.service` as long-lived fields.

## Integration-branch checklist

The future branch that combines all active work should:

1. reuse the canonical Skill/Research services if another branch already composes them;
2. call `build_single_node_learning(...)` after Evaluation, Verification, routing and Approval are available;
3. preserve or explicitly review the `LearningPlatformPolicy` floor rather than inheriting Candidate policy as platform authority;
4. register the Learning composition on the final composed Control Plane;
5. register `add_learning_parser(...)` / `execute_learning(...)` in the final CLI dispatcher;
6. mount the prepared `/learning` routes in the final Shell/navigation;
7. supply a target-aware `ConfigurationSnapshot` factory only if post-promotion Evaluation is enabled;
8. then run the repository-wide formatter, typecheck, tests and required CI once on the unified branch.

This issue branch intentionally does not treat CI/test execution as completion evidence because validation is being deferred to that unified integration branch.
