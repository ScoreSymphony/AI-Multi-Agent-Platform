# Automatic reviewer-Agent workflows

Issue #711 connects the canonical Verification subsystem from #86 to normal Agent/AgentTeam execution without creating another review lifecycle.

## Canonical path

The productive path is:

```text
producer Agent/Team
  -> canonical Result/Artifact
  -> VerificationRequest
  -> AutomaticReviewerWorkflow
  -> exact reviewer Agent/Team revision
  -> normal AgentRuntime + replaceable orchestrator/provider execution
  -> ReviewerExecutionDecision
  -> ReviewerAgentRuntime
  -> canonical VerificationResult
  -> VerificationCompletionAuthority
  -> accepted / waiting / repair-required / rejected / escalated
```

`AutomaticReviewerWorkflow` is coordination only. `VerificationService` remains the review authority and `VerificationCompletionAuthority` remains the deterministic Task-completion gate. A reviewer Agent cannot mark a Task accepted directly.

## Reviewer selection

`ConfiguredReviewerResolver` maps an exact `(policy_id, policy_version, stage_id)` to a `ReviewerAssignment`.

Supported routes are:

- exact standalone Agent ID + revision;
- exact AgentTeam ID + revision + exact member Agent ID/revision;
- exact AgentTeam ID + revision + unique role such as `reviewer`.

Team-role resolution must yield exactly one member. Missing or ambiguous configuration fails closed. There is deliberately no hidden fallback to the bundled Reviewer from #77, so cloned/custom Reviewers work identically and removing the bundled definition does not change architecture.

## Replaceable reviewer execution

The coordinator starts a reviewer through the existing `ReviewerAgentRuntime`, which in turn uses normal `AgentRuntime` preparation and orchestration mapping. The provider/orchestrator-specific execution is represented by `ReviewerAgentExecutor`.

An executor receives the exact `VerificationRequest` and pinned `AgentRunRecord` and returns a structured `ReviewerExecutionDecision`. The coordinator submits that decision through `ReviewerAgentRuntime.complete_review()`. The execution adapter never receives completion authority.

This keeps local/reference, Hermes, alternative orchestrators and different model providers behind the same canonical workflow.

## Independence and privileges

All #86 verifier checks remain active in the productive path. Policies can require the reviewer to differ from the producer Agent, model or provider and can require read-only capabilities. Failure to prove a required condition blocks reviewer creation or result submission.

Review assignment does not grant write, shell, merge, deploy or administrative authority. Any later privileged repair or delivery action still passes the normal Authorization/Approval boundaries.

## Bounded repair and re-review

If canonical completion assessment returns `repair_required` and a `ReviewerRepairExecutor` is configured, the coordinator invokes it with the exact request, structured VerificationResult/findings and completion decision.

The repair executor is intentionally a boundary rather than a second lifecycle engine. Its implementation must perform repair through the normal canonical Task/Plan/Step/Run mechanisms and return only a reference to the newly produced Result/Artifact.

The coordinator then calls `CanonicalVerificationRuntime.request_reverification_after_repair()`. That runtime resolves the new subject from canonical evidence, binds a fresh exact revision/digest and increments the #86 repair attempt. The next review therefore cannot reuse an old Verification for modified output.

When the #86 repair budget is exhausted, completion becomes rejected/escalated/waiting according to the configured policy and the coordinator does not start another repair cycle.

## Duplicate dispatch and restart behavior

Reviewer correlation is derived from the immutable `AgentRunRecord.verification_context` written by `ReviewerAgentRuntime`; no second workflow-state database is introduced.

Before dispatch, the coordinator scans existing canonical AgentRuns for the Verification ID:

- no existing reviewer run: resolve and start the exact reviewer;
- one running reviewer run: return the current blocked state without starting or re-executing another reviewer;
- a terminal reviewer run with a still-pending Verification: fail closed and require reconciliation;
- more than one bound reviewer run: report a contract violation;
- completed Verification: reuse the canonical VerificationResult and never execute the reviewer again.

This intentionally prioritizes correctness over automatically replaying an external reviewer after an uncertain restart boundary.

## Product/status surface

`ReviewWorkflowResult` exposes the canonical request, bound reviewer AgentRun, VerificationResult and current `CompletionGateDecision`. `status_for()` reads this state without executing or retrying work.

The existing Verification Control Plane collections continue to expose canonical request/result/completion data. Product clients should use those canonical resources plus normal AgentRun resources rather than calling reviewer-private internals.

## Integration sketch

```python
workflow = AutomaticReviewerWorkflow(
    runtime=canonical_verification_runtime,
    completion=verification_completion_authority,
    agents=agent_runtime,
    resolver=configured_reviewer_resolver,
    executor=reviewer_execution_adapter,
    repair_executor=repair_adapter,
)

result = await workflow.request_and_run(
    task_id=task_id,
    policy_id=policy.policy_id,
    policy_version=policy.version,
    stage_id="review",
    subject_type="result",
    subject_id=result_id,
    correlation_id=correlation_id,
)
```

The caller is responsible for choosing the Verification policy as part of normal Task/Agent configuration. Tasks without an Agent-verifier policy do not enter this workflow.

## Non-authorities

The following remain explicitly non-authoritative:

- reviewer prose;
- orchestrator-native review state;
- provider session IDs;
- AgentTeam membership by itself;
- the workflow coordinator's returned status object.

Only canonical Verification records and the existing completion authority decide whether required review has passed.
