# Automatic reviewer-Agent workflows

Issue #711 connects the canonical Verification subsystem from #86 to normal Agent/AgentTeam execution without creating another review lifecycle.

The integration is intentionally imported from `ai_multi_agent_platform.verification.agent_workflow`, not re-exported from `verification.__init__`. The existing reviewer and repair bridges have the same package-boundary rule because they depend on Agent/Kernel runtime code and a top-level re-export can introduce a `verification -> agents/control_plane -> kernel -> verification` import cycle.

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
- exact AgentTeam ID + revision + unique role.

The bundled Software Development Team from #77 is supported through its actual `reviewer_tester` role, but it is not a hidden default. Team-role resolution must yield exactly one member. Missing or ambiguous configuration fails closed, so cloned/custom Reviewers work identically and removing the bundled definition does not change architecture.

## Replaceable reviewer execution

The coordinator starts a reviewer through the existing `ReviewerAgentRuntime`, which in turn uses normal `AgentRuntime` preparation and orchestration mapping. Provider/orchestrator-specific execution remains behind `ReviewerAgentExecutor`.

An executor receives the exact `VerificationRequest` and pinned `AgentRunRecord` and returns a structured `ReviewerExecutionDecision`. The coordinator submits that decision through `ReviewerAgentRuntime.complete_review()`. The execution adapter never receives completion authority.

### Local/reference ModelRuntime executor

`ai_multi_agent_platform.verification.reference_reviewer.ModelRuntimeReviewerExecutor` is the local-first reference implementation. It executes the already-pinned reviewer through the canonical provider-neutral `ModelRuntime`; it does not assume Hermes, Forge, LiteLLM, OpenAI, or any other provider.

Reviewable content is supplied through `ReviewerSubjectInputProvider`. The provider must return a `ReviewerSubjectInput` carrying the exact `VerificationSubject`. The executor compares that subject to the immutable request before invoking the model, so stale or mismatched content fails closed. This boundary lets repository-, file-, result-, or application-specific subject renderers be replaced independently of Verification semantics.

`ai_multi_agent_platform.verification.reviewer_input.KernelFileReviewerSubjectInputProvider` is the concrete canonical reference input provider. For Results it reads the exact producer Run projection pinned on the Verification request and requires the expected `run_id:attempt` revision. For Artifacts it resolves the exact canonical file revision through `FileProvider`, checks Artifact linkage, ready state, SHA-256 and checksum, preserves the file classification, and reads only bounded UTF-8 evidence. The default input limit is 256 KiB. This keeps the reference path local-first while preventing the model from choosing a different or stale subject.

The reviewed content is presented to the model as untrusted data. The platform, not the model, owns the Verification ID, exact subject revision/digest, reviewer identity, Evidence Artifact IDs, and completion state. The model returns only a structured outcome and findings. Malformed/unknown output is rejected rather than guessed.

The reference executor uses the model configuration already pinned on the reviewer `AgentRun`. A local `ModelConfiguration(location=local)` therefore provides a complete automatic-review path without a mandatory paid/external AI service. Other provider/model implementations can replace it without changing the canonical workflow.

## Independence and privileges

All #86 verifier checks remain active in the productive path. Policies can require the reviewer to differ from the producer Agent, model or provider and can require read-only capabilities. Failure to prove a required condition blocks reviewer creation or result submission.

Review assignment does not grant write, shell, merge, deploy or administrative authority. Any later privileged repair or delivery action still passes the normal Authorization/Approval boundaries.

## Bounded repair and re-review

Automatic repair is available only when both the existing `VerificationRepairRuntime` and a `ReviewerRepairExecutor` are supplied. Partial repair composition is rejected instead of silently bypassing the canonical repair path.

When canonical completion assessment returns `repair_required`, the coordinator first calls `VerificationRepairRuntime.start_repair()`. That existing #86 bridge performs the canonical repair transition through ordinary kernel operations:

1. replan the Task through the replaceable orchestrator;
2. resume the Verification-waiting Task;
3. create an exact repair Step/Run;
4. start the Run through the normal lifecycle backend.

Only after that canonical repair Run exists does `ReviewerRepairExecutor.execute_repair()` run or await the producer/Worker work associated with it. The executor receives the immutable source Verification request/result and `VerificationRepairExecution`; it cannot invent a second repair identity. It returns only the newly produced canonical Result/Artifact reference.

The coordinator then calls `CanonicalVerificationRuntime.request_reverification_after_repair()`. That runtime resolves the new subject from canonical evidence, binds a fresh exact revision/digest and increments the #86 repair attempt. The next review therefore cannot reuse an old Verification for modified output.

When the #86 repair budget is exhausted, completion becomes rejected/escalated/waiting according to the configured policy and the coordinator does not start another repair cycle.

## Dispatch, cancellation and recovery

Reviewer correlation is derived from the immutable `AgentRunRecord.verification_context` written by `ReviewerAgentRuntime`; no second workflow-state database is introduced.

Dispatch for a Verification ID is serialized across `AutomaticReviewerWorkflow` instances sharing the same canonical Agent repository in the current Control Plane process. The durable AgentRun binding remains the recovery source of truth after the process restarts. A second caller therefore observes the already-created reviewer run rather than starting another model execution.

The execution/result handoff is deliberately recoverable:

1. the reviewer runs through `ReviewerAgentExecutor`;
2. its structured `ReviewerExecutionDecision` is persisted in AgentRun telemetry before canonical result submission;
3. `ReviewerAgentRuntime.complete_review()` terminalizes the AgentRun and submits the canonical VerificationResult;
4. if canonical submission fails after model execution, a later invocation reuses the staged decision and retries submission without re-running the reviewer model.

Cancellation before a reviewer decision is produced marks that AgentRun `CANCELLED`. A subsequent invocation may create a fresh reviewer attempt. Ordinary reviewer execution failures similarly become `FAILED`. `ReviewerRuntimeOptions.max_reviewer_attempts` bounds repeated failed/cancelled reviewer attempts; the default is two attempts. Exhaustion fails closed with a canonical error while Verification remains unresolved.

If cancellation happens after a decision has already been staged, the decision remains recoverable. Depending on the exact interruption point the AgentRun may still be running or already succeeded; both states can resume canonical submission from the staged decision without another model call.

A pre-existing running reviewer AgentRun without a staged decision is treated as in-flight work and leaves completion blocked instead of being executed a second time. Completed Verification always reuses its immutable canonical VerificationResult.

The process-local serialization matches the current reference AgentRepository concurrency boundary. Deployments with multiple independent Control Plane writers must provide the same uniqueness/claim guarantee at their durable Agent repository boundary before enabling concurrent reviewer dispatch across processes.

## Product/status surface

`ReviewWorkflowResult` exposes the canonical request, bound reviewer AgentRun, VerificationResult, optional canonical `VerificationRepairExecution`, and current `CompletionGateDecision`. `status_for()` reads review state without executing or retrying work.

The existing Verification Control Plane collections continue to expose canonical request/result/completion data. Normal AgentRun resources serialize their `verification_context`, including the Verification ID and exact subject binding, so clients can correlate pending/running reviewer AgentRuns without a second review-state API or database. Reviewer decision staging lives in ordinary AgentRun telemetry and is recovery evidence, not a competing completion authority.

## Integration sketch

```python
from ai_multi_agent_platform.verification.agent_workflow import AutomaticReviewerWorkflow
from ai_multi_agent_platform.verification.reference_reviewer import ModelRuntimeReviewerExecutor
from ai_multi_agent_platform.verification.reviewer_input import (
    KernelFileReviewerSubjectInputProvider,
)

review_inputs = KernelFileReviewerSubjectInputProvider(
    tasks=task_repository,
    runs=run_repository,
    files=file_provider,
)
review_executor = ModelRuntimeReviewerExecutor(
    agents=agent_runtime,
    models=model_runtime,
    inputs=review_inputs,
)
workflow = AutomaticReviewerWorkflow(
    runtime=canonical_verification_runtime,
    completion=verification_completion_authority,
    agents=agent_runtime,
    resolver=configured_reviewer_resolver,
    executor=review_executor,
    repair_runtime=verification_repair_runtime,
    repair_executor=repair_execution_adapter,
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
- `ReviewerExecutionDecision` and its staged recovery copy;
- the workflow coordinator's returned status object.

Only canonical Verification records and the existing completion authority decide whether required review has passed. Only the kernel/`VerificationRepairRuntime` owns the repair Plan/Step/Run transition.
