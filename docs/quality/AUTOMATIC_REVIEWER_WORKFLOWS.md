# Automatic reviewer-Agent workflows

Issue #711 connects the canonical Verification subsystem from #86 to normal Agent/AgentTeam execution without creating another review lifecycle.

The reviewer/repair integration remains an explicit submodule boundary because it depends on Agent and Kernel runtime code. It is not re-exported wholesale from `verification.__init__`, which preserves the existing package dependency boundary.

## Canonical productive path

The normal configured path is now:

```text
producer Agent/Team
  -> canonical Result/Artifact
  -> PlatformKernel.attach_result()/attach_artifact()
  -> canonical result.attached/artifact.attached event is persisted
  -> provider-neutral OutputAttachmentObserver
  -> AutomaticReviewerOutputCoordinator
  -> exact canonical VerificationRequest
  -> AutomaticReviewerWorkflow
  -> exact reviewer Agent/AgentTeam revision
  -> normal AgentRuntime + replaceable ModelRuntime/provider execution
  -> ReviewerExecutionDecision
  -> ReviewerAgentRuntime
  -> canonical VerificationResult
  -> VerificationCompletionAuthority
  -> accepted / waiting / repair-required / rejected / escalated
```

The ordinary producer API remains `PlatformKernel.attach_result()` / `attach_artifact()`. Product code does not need to call `request_verification()`, `ReviewerAgentRuntime`, `AutomaticReviewerWorkflow.run_request()`, or `complete_task()` to drive a configured successful automatic review.

`AutomaticReviewerWorkflow`, the output observer, and the coordinator are coordination only. `VerificationService` remains the review authority, `VerificationCompletionAuthority` remains the deterministic Task-completion gate, and the kernel remains canonical lifecycle authority. A reviewer Agent cannot mark a Task accepted directly.

## Kernel output observer and crash recovery

The public `PlatformKernel` provides a provider-neutral post-commit `OutputAttachmentObserver` seam. The observer is invoked only after the canonical `result.attached` or `artifact.attached` event is durable. The kernel passes the persisted event to the observer and contains no Verification-, Agent-, model-, or reviewer-specific policy logic.

This ordering deliberately makes the persistence/dispatch boundary recoverable. If output attachment succeeds but observation/review fails or the process stops immediately afterward, retrying the same attachment command with the same idempotency key replays observation from the already-persisted canonical event without appending a duplicate attachment event. Reusing that idempotency key with a different output ID or Run binding fails closed with `CONFLICT` rather than reviewing a different subject under the old command identity.

The Verification-side `AutomaticReviewerOutputObserver` translates only persisted Result/Artifact attachment events into `AutomaticReviewerOutputCoordinator.review_attached_subject()`. It never re-attaches the output, so the hook cannot recursively trigger itself.

The normal durable single-node deployment installs this observer and its local reference reviewer composition automatically. Automatic review itself remains opt-in per versioned Verification policy, so installing the observer does not force reviews on unrelated Tasks.

## Policy-driven automatic reviewer selection

Automatic review is explicitly enabled in versioned `VerificationPolicy.metadata`. There is no hidden bundled Reviewer fallback.

A representative policy configuration is:

```python
VerificationPolicy(
    name="software-output-review",
    stages=(VerificationStage("review", VerifierKind.AGENT),),
    metadata={
        "automatic_reviewer": {
            "enabled": True,
            "subject_types": ["result"],
            "stages": {
                "review": {
                    "agent_id": reviewer_agent_id,
                    "agent_revision": reviewer_agent_revision,
                }
            },
        }
    },
)
```

`subject_types` may contain `result`, `artifact`, or both. Every automatically executed AGENT stage must resolve to an exact reviewer assignment. Supported assignments are:

- exact standalone Agent ID + revision;
- exact AgentTeam ID + revision + exact member Agent ID/revision;
- exact AgentTeam ID + revision + one unique Team role.

`PolicyMetadataReviewerResolver` reads this versioned configuration and delegates exact resolution to the same canonical reviewer-assignment rules used by `ConfiguredReviewerResolver`. Unknown fields, invalid revisions, missing stage assignments, disabled Teams, or ambiguous Team-role matches fail closed.

The bundled Software Development Team from #77 is supported through its real `reviewer_tester` role, but remains only a replaceable reference configuration. Cloned/custom Reviewer Agents and Teams behave identically.

A Task without a Verification requirement, a policy without `automatic_reviewer` metadata, a disabled automatic-review configuration, or an output type not selected by that configuration follows the normal no-review path.

## Replaceable reviewer execution

The coordinator starts a reviewer through the existing `ReviewerAgentRuntime`, which uses normal `AgentRuntime` preparation and the exact pinned Agent revision. Provider/model execution remains replaceable behind `ReviewerAgentExecutor`.

`ai_multi_agent_platform.verification.reference_reviewer.ModelRuntimeReviewerExecutor` is the local-first reference implementation. It invokes the canonical provider-neutral `ModelRuntime`; it does not require Hermes, Forge, LiteLLM, OpenAI, or another specific provider.

Reviewable content is supplied through `ReviewerSubjectInputProvider`. `KernelFileReviewerSubjectInputProvider` is the canonical local reference provider:

- Results are loaded from the exact producer Run bound to the Verification request.
- Immediately before model invocation, the provider reconstructs the same canonical Result snapshot used by #86, recomputes its SHA-256 digest, and rejects the input if it no longer equals the immutable Verification subject digest.
- The model receives only Result fields covered by that canonical snapshot/digest.
- Artifacts are resolved through `FileProvider` using the exact canonical file revision, Artifact linkage, ready state, SHA-256 metadata and checksum.
- Artifact data classification is preserved.
- Input is bounded; the default limit is 256 KiB.
- The reference Artifact path accepts bounded UTF-8 textual evidence and fails closed for unsupported binary input.

The reviewed content is presented to the model as untrusted data. The platform, not the model, owns the Verification ID, exact subject revision/digest, reviewer identity, Evidence Artifact IDs, and completion state. Model output is restricted to the structured review outcome/findings schema; malformed or unknown output is rejected rather than guessed.

A locally configured `ModelConfiguration(location=local)` therefore provides the complete reference automatic-review path without a mandatory paid/external AI service.

## Independence and privileges

All #86 verifier-independence checks remain active in the productive path. Policies can require, independently or together:

- reviewer Agent != producer Agent;
- reviewer model != producer model;
- reviewer provider != producer provider;
- read-only reviewer capabilities;
- self-verification restrictions.

Failure to prove a required condition blocks reviewer creation or result submission. Review assignment does not grant write, shell, merge, deploy or administrative authority. Later privileged repair/delivery operations still pass ordinary Authorization/Approval boundaries.

## Bounded repair and re-review

Automatic repair is available only when both the existing `VerificationRepairRuntime` and a `ReviewerRepairExecutor` are supplied. Partial repair composition is rejected.

When canonical completion assessment returns `repair_required`, the workflow first calls `VerificationRepairRuntime.start_repair()`. The canonical bridge creates the repair Plan/Step/Run through ordinary kernel operations. Only then may `ReviewerRepairExecutor` perform or await the repair work.

The resulting `RepairOutput` is staged durably in the bound reviewer AgentRun telemetry **before** a fresh reverification request is created. If the workflow stops after repair output exists but before `request_reverification_after_repair()` succeeds, a later invocation validates and reuses that exact staged output instead of executing repair again. The staged identity includes the source Verification, repair Run and repair attempt.

The repaired Result/Artifact is then resolved again through canonical evidence, producing a fresh exact Verification subject/revision/digest. An older Verification can never certify modified output. The #86 repair budget remains authoritative and bounds the loop.

## Dispatch, cancellation and recovery

Reviewer correlation is derived from immutable `AgentRunRecord.verification_context`; no second workflow-state database is introduced.

Within the current single-Control-Plane process, dispatch for one Verification ID is serialized across workflow instances sharing the same canonical Agent repository. Per-Verification lock holders are weakly retained: active/waiting callers keep the same lock strongly referenced, while completed IDs can be garbage-collected instead of accumulating for the lifetime of the process. Durable AgentRun bindings remain the restart/recovery source of truth.

The reviewer result handoff is also recoverable:

1. the reviewer executes through `ReviewerAgentExecutor`;
2. the structured `ReviewerExecutionDecision` is persisted in AgentRun telemetry;
3. `ReviewerAgentRuntime.complete_review()` terminalizes the AgentRun and submits the canonical VerificationResult;
4. if canonical submission fails after model execution, a later invocation resubmits the staged decision without a second model call.

Direct `asyncio.CancelledError` and canonical `ContractError(ErrorCode.CANCELLED)` returned by `ModelRuntime` both use cancellation semantics. Before a decision is staged, the reviewer AgentRun becomes `CANCELLED` rather than `FAILED`; a later invocation may use a fresh bounded attempt. Other reviewer execution failures become `FAILED`. `ReviewerRuntimeOptions.max_reviewer_attempts` bounds repeated failed/cancelled attempts; the default is two.

If cancellation occurs after a decision has already been staged, the decision remains recoverable. A pre-existing running reviewer without a staged decision is treated as in-flight work and is not executed a second time. Completed Verification reuses its immutable canonical result.

The process-local serialization matches the current reference AgentRepository write boundary. A future deployment with multiple independent Control Plane writers must provide an equivalent durable uniqueness/claim guarantee at the AgentRepository boundary before enabling concurrent cross-process reviewer dispatch.

## Product/status surface

`ReviewWorkflowResult` exposes the canonical request, bound reviewer AgentRun, VerificationResult, optional canonical `VerificationRepairExecution`, and current `CompletionGateDecision`. `status_for()` reads review state without executing/retrying work.

The existing Verification Control Plane collections remain the public source for canonical request/result/completion state. Normal AgentRun resources expose their `verification_context`, allowing clients to correlate the reviewer execution with its exact Verification and subject. Staged reviewer/repair data in AgentRun telemetry is recovery evidence, not competing lifecycle authority.

## Normal integration example

In the standard durable single-node composition the observer/executor is already installed. Product code configures the policy/Task requirement and then uses the normal output attachment API:

```python
policy = verification.register_policy(
    VerificationPolicy(
        name="automatic-review",
        stages=(VerificationStage("review", VerifierKind.AGENT),),
        metadata={
            "automatic_reviewer": {
                "enabled": True,
                "subject_types": ["result"],
                "stages": {
                    "review": {
                        "agent_id": reviewer_agent_id,
                        "agent_revision": reviewer_agent_revision,
                    }
                },
            }
        },
    )
)
verification_runtime.require_task(
    task_id=task_id,
    policy_id=policy.policy_id,
    policy_version=policy.version,
)

# After the producer Run has created the canonical Result, this single normal kernel call
# persists the output and drives the configured reviewer workflow.
task = await kernel.attach_result(
    idempotency_key=output_command_key,
    task_id=task_id,
    run_id=producer_run_id,
    result_id=result_id,
)
```

If the reviewer passes and the Task is waiting only on Verification, the coordinator asks the normal kernel completion path to finish it. If review remains pending, requires repair, fails or escalates, canonical Verification keeps completion blocked according to policy.

## Non-authorities

The following remain explicitly non-authoritative:

- reviewer prose;
- orchestrator-native review state;
- provider session IDs;
- AgentTeam membership by itself;
- `ReviewerExecutionDecision` and staged recovery copies;
- the output observer/coordinator return objects.

Only canonical Verification records and `VerificationCompletionAuthority` decide whether required review has passed. Only the kernel and `VerificationRepairRuntime` own canonical repair Plan/Step/Run transitions.
