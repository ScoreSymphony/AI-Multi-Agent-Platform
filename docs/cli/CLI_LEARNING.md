# Governed Learning CLI

Issue: #595

The Learning CLI is API-first. It reads and mutates governed Learning state only through the canonical `/api/v1` Control Plane. It never imports Learning repositories or owner-domain storage directly.

## Public entrypoint

The distributed `platform` command registers the first-class governed Learning domain through `src/ai_multi_agent_platform/cli/app.py` and `src/ai_multi_agent_platform/cli/learning.py`.

```bash
platform learning --help
platform learning candidate list
```

The existing generic extension commands remain available for read-only inspection of registered Learning collections:

```bash
platform extension list learning-candidates
platform extension show learning-candidates <learning_candidate_id>
platform extension list learning-feedback
platform extension list learning-post-promotion-evaluations
platform extension commands
```

The generic extension framework still supports `extension execute` for other registered domains. The public `platform` entrypoint explicitly rejects `platform extension execute learning.*`, however, because a generic executor cannot preserve Learning-specific confirmation and governance semantics. Every governed Learning mutation must use the first-class `platform learning` adapter instead.

## First-class Learning commands

### Candidate inspection

```bash
platform learning candidate list
platform learning candidate show learning_candidate_...
```

### Feedback

```bash
platform learning feedback list
platform learning feedback show feedback_...

platform learning feedback create \
  --feedback-type correction \
  --subject-json '{"kind":"run_result","resource_id":"result_...","revision":"1","digest":"sha256:..."}' \
  --comment "The bounded method should be used here." \
  --idempotency-key feedback-correction-1
```

Supported feedback types follow the canonical `FeedbackType` contract:

```text
correction
outcome_accepted
outcome_rejected
preference
rating
finding
comment
```

Feedback is immutable evidence. Recording it does not create mutation authority by itself.

### Operator proposal

```bash
platform learning propose \
  --problem "Repeated failures use the wrong method." \
  --target-json '{"resource_type":"agent","resource_id":"agent_...","revision":3}' \
  --improvement-type owner_revision \
  --expected-benefit "Reduce the repeated failure category." \
  --risk standard \
  --gate-plan-json '{"policy_id":"learning.default","policy_version":1,"require_evaluation":true,"require_verification":false,"require_regression_free":true,"approval_required_risks":["high","critical"],"automatic_promotion_allowed":false,"evaluation_suite_refs":["learning-suite@1"],"verification_policy_refs":[]}' \
  --proposed-change-json '{"role_instruction":"Use the evaluated bounded method."}' \
  --idempotency-key learning-proposal-1
```

Risk values are the canonical #15 values:

```text
standard
elevated
high
critical
```

The Candidate `gate_plan` is not the platform authority. The deployment-owned `LearningPlatformPolicy` is enforced separately and can require stricter Evaluation/Verification bindings or Approval. In the default production composition, Evaluation/Verification references must use exact `<id>@<version>` form when their gate is enabled, HIGH/CRITICAL promotions require Approval, global/unscoped promotions require Approval, and automatic promotion is disabled platform-wide.

### Proposal from exact feedback

```bash
platform learning propose-from-feedback feedback_... \
  --problem "The prior output used the wrong method." \
  --target-json '{"resource_type":"agent","resource_id":"agent_...","revision":3}' \
  --improvement-type instruction_correction \
  --expected-benefit "Apply the explicit correction on equivalent tasks." \
  --risk standard \
  --gate-plan-json '{...}' \
  --proposed-change-json '{...}' \
  --idempotency-key learning-from-feedback-1
```

### Bind Evaluation / Verification evidence

```bash
platform learning evidence learning_candidate_... \
  --expected-revision 2 \
  --evaluation-run-id evaluation_run_... \
  --verification-id verification_... \
  --idempotency-key learning-evidence-1
```

The server resolves these IDs through the canonical Evaluation and Verification services and records exact references on a new Learning Candidate revision.

### Accept or reject

```bash
platform learning accept learning_candidate_... \
  --expected-revision 3 \
  --idempotency-key learning-accept-1

platform learning reject learning_candidate_... \
  --expected-revision 3 \
  --idempotency-key learning-reject-1
```

Acceptance reruns the candidate's versioned quality gate and the deployment-owned governance floor. A failed/missing Evaluation or Verification result, or an invalid non-versioned required suite/policy binding, blocks acceptance.

### Supersede

```bash
platform learning supersede learning_candidate_old \
  --superseded-by learning_candidate_new \
  --expected-revision 2 \
  --idempotency-key learning-supersede-1
```

### Promote

Promotion is a side-effecting operation and therefore uses the global explicit-confirmation flag:

```bash
platform --yes learning promote learning_candidate_... \
  --expected-revision 4 \
  --idempotency-key learning-promote-1
```

When either the Candidate policy or the platform governance floor requires explicit Approval, the first authorized promotion attempt can return the canonical pending Approval reference. With the default platform policy this includes HIGH/CRITICAL candidates and global/unscoped targets. After that exact action has been approved through the normal Approval surface, retry with the bound Approval ID:

```bash
platform --yes learning promote learning_candidate_... \
  --expected-revision 4 \
  --approval-id approval_... \
  --idempotency-key learning-promote-approved-1
```

The CLI requires `--yes` before sending promotion to the Control Plane. The server still owns stale-revision checking, exact-action Approval binding, authorization, quality validation, platform-policy validation and canonical owner revision creation.

### Post-promotion Evaluation

When the optional derived collection is registered:

```bash
platform learning post-eval list
platform learning post-eval show learning_post_evaluation_...
```

These records are derived Evaluation evidence only. They do not rewrite the promoted resource or historical Learning Candidate revisions. In the Single-Node composition they are persisted in SQLite and the runtime suppresses duplicate evaluation for the same promoted Candidate revision/target revision pair.

## Production integration

The public CLI composition now:

1. imports `add_learning_parser` and `execute_learning`;
2. registers `platform learning` exactly once alongside the Registry domain;
3. dispatches it through the same authenticated `ControlPlaneClient` and confirmation helper as other top-level domains;
4. keeps generic extension inspection available while blocking `extension execute learning.*` from bypassing the domain adapter;
5. preserves the deployment-owned `LearningPlatformPolicy` floor;
6. delegates every unrelated CLI area unchanged to the existing lower-level composition.

Regression coverage in `tests/test_issue_81_cli_entrypoint.py` proves that candidate inspection reaches `/api/v1/learning-candidates`, promotion requires global `--yes` before transport, confirmed promotion dispatches the exact candidate revision and optional Approval binding, and generic extension execution cannot bypass the Learning domain safeguards.
