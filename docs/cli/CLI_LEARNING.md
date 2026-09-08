# Governed Learning CLI

Issue: #595

The Learning CLI is API-first. It reads and mutates governed Learning state only through the canonical `/api/v1` Control Plane. It never imports Learning repositories or owner-domain storage directly.

## Read-only inspection

The existing generic extension commands can inspect registered Learning collections:

```bash
platform extension list learning-candidates
platform extension show learning-candidates <learning_candidate_id>
platform extension list learning-feedback
platform extension list learning-post-promotion-evaluations
```

`platform extension commands` can show which canonical extension commands are registered.

The generic extension CLI is deliberately read-only. It does not execute `POST /api/v1/commands/{command}` because a generic mutation layer cannot know a domain's confirmation, Approval and recovery semantics.

## First-class Learning commands

`src/ai_multi_agent_platform/cli/learning.py` prepares the explicit `platform learning` domain adapter. The later unified integration branch must register `add_learning_parser(...)` and dispatch `execute_learning(...)` in the final CLI composition.

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

Acceptance reruns the candidate's versioned quality gate. A failed or missing required Evaluation/Verification result blocks acceptance.

### Supersede

```bash
platform learning supersede learning_candidate_old \
  --superseded-by learning_candidate_new \
  --expected-revision 2 \
  --idempotency-key learning-supersede-1
```

### Promote

```bash
platform learning promote learning_candidate_... \
  --expected-revision 4 \
  --idempotency-key learning-promote-1
```

For a risk class that requires explicit Approval, the first attempt returns the canonical pending Approval reference. After that exact action has been approved through the normal Approval surface, retry with the bound Approval ID:

```bash
platform learning promote learning_candidate_... \
  --expected-revision 4 \
  --approval-id approval_... \
  --idempotency-key learning-promote-approved-1
```

The CLI invokes its normal explicit confirmation hook before promotion. The server still owns stale-revision checking, exact-action Approval binding, authorization, quality validation and canonical owner revision creation.

### Post-promotion Evaluation

When the optional derived collection is registered:

```bash
platform learning post-eval list
platform learning post-eval show learning_post_evaluation_...
```

These records are derived Evaluation evidence only. They do not rewrite the promoted resource or historical Learning Candidate revisions.

## Unified-branch integration

This issue branch intentionally leaves the top-level CLI dispatcher untouched to reduce conflicts with other active branches. When the active branches are combined, the final CLI composition should:

1. import `add_learning_parser` and `execute_learning`;
2. register `add_learning_parser(areas)` once;
3. dispatch the `learning` area to `execute_learning(args, client, confirm)` using the existing authenticated Control Plane client and confirmation helper;
4. retain the generic extension CLI as read-only;
5. run the unified branch's formatting/type/test/CI pass only after all active branches are reconciled.
