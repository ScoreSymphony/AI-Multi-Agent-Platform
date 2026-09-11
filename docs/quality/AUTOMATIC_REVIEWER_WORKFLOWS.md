# Automatic reviewer-Agent workflows

Issue #711 connects the canonical Verification subsystem from #86 to normal Agent/AgentTeam execution without creating another review lifecycle. Issue #759 completes deterministic scoped reviewer discovery, productive Artifact repair/re-review, replaceability coverage and the client-visible reviewer-selection contract.

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

The Verification-side `AutomaticReviewerOutputObserver` translates only persisted Result/Artifact attachment events into `AutomaticReviewerOutputCoordinator.review_attached_subject()`. It never re-attaches ordinary producer output, so the hook cannot recursively trigger itself.

The normal durable single-node deployment installs this observer and its local reference reviewer composition automatically. Automatic review itself remains opt-in per versioned Verification policy, so installing the observer does not force reviews on unrelated Tasks.

## Policy-driven automatic reviewer selection

Automatic review is explicitly enabled in versioned `VerificationPolicy.metadata`. There is no hidden bundled Reviewer fallback.

An exact-assignment policy can pin one immutable reviewer identity directly:

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

`subject_types` may contain `result`, `artifact`, or both. Every automatically executed AGENT stage must configure exactly one reviewer route. Exact routes support:

- exact standalone Agent ID + revision;
- exact AgentTeam ID + revision + exact member Agent ID/revision;
- exact AgentTeam ID + revision + one unique Team role.

Issue #759 additionally supports deterministic role/capability discovery inside an explicit policy-owned candidate scope. A scoped route uses stable candidate identities and resolves their current canonical revisions exactly once at dispatch:

```python
VerificationPolicy(
    name="scoped-software-review",
    stages=(VerificationStage("review", VerifierKind.AGENT),),
    metadata={
        "automatic_reviewer": {
            "enabled": True,
            "subject_types": ["result", "artifact"],
            "stages": {
                "review": {
                    "candidate_agent_ids": [custom_reviewer_id],
                    "candidate_team_ids": [software_team_id],
                    "reviewer_role": "reviewer_tester",
                    "required_capability_ids": ["tool.file.read"],
                }
            },
        }
    },
)
```

Scoped discovery is deliberately bounded: it never scans arbitrary global Agents. At least one candidate Agent/Team identity and at least one semantic selector (`reviewer_role` and/or `required_capability_ids`) are required. Exact-assignment fields and scoped-discovery fields cannot be mixed in one stage.

`PolicyMetadataReviewerResolver` validates the versioned route and delegates either to `ConfiguredReviewerResolver` for exact assignments or `CapabilityRoleReviewerResolver` for scoped discovery. Scoped discovery succeeds only when exactly one enabled candidate matches; zero matches, multiple matches, missing definitions, disabled candidates or disabled Teams fail closed. Exact routing likewise rejects disabled Agent/Team definitions before a resolved reviewer is returned. The bundled Software Development Team from #77 may participate through its real `reviewer_tester` role, but remains only replaceable configuration and is never an implicit fallback.

The selected exact Agent revision, selected Team revision where applicable, configured/discovery mode and policy selection criteria are persisted as `reviewer-selection-v1` provenance inside the canonical reviewer AgentRun verification context. This makes the selection explainable and restart-verifiable without granting the routing layer Verification authority.

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

The normal durable single-node composition supplies the existing `VerificationRepairRuntime` together with `ProducerAgentRepairBindingProvider` and `KernelAgentRepairExecutor`. Other deployments can replace those adapters, but partial repair composition is rejected: `AutomaticReviewerWorkflow` requires both a repair runtime and a repair executor or neither.

When canonical completion assessment returns `repair_required`, the workflow calls `VerificationRepairRuntime.start_repair()`. If the Task still carries an older `verification:waiting` projection, the repair runtime first calls the ordinary kernel completion path so the current `REPAIR_REQUIRED` decision is persisted as `verification:repair_required`; it does not mutate private Task state.

The repair runtime then replans through the normal orchestrator. Once the exact repair Step ID exists, `ProducerAgentRepairBindingProvider` binds that Step to the exact canonical producer Agent identity from the failed Verification request **before** the Task is resumed and before the repair Run is dispatched. The binding preserves the producer model identity and canonical capability evidence rather than letting reviewer text select execution authority.

Reviewer findings, evidence IDs and the exact reviewed subject are added to the repair objective as bounded, explicitly untrusted diagnostic context. They are not policy, authorization, capability grants or executable control data. The public reference path rejects repair when exact producer Agent identity cannot be established.

`VerificationRepairRuntime` then resumes the Task, creates one canonical repair Step/Run and starts it through the normal lifecycle backend. `KernelAgentRepairExecutor` reconciles that existing Run; it never creates a parallel repair lifecycle. The successful Agent Run must expose exactly one canonical output of the same kind being repaired: a Result repair requires `result_id`, while an Artifact repair accepts one canonical `artifact_id`/`artifact_refs` identity and rejects missing, malformed or ambiguous Artifact output. The resolved Result or Artifact is attached to that same terminal repair Run before re-verification; an Artifact repair is never fabricated as a Result.

That internal repaired-output attachment uses provenance source `verification-repair`. `AutomaticReviewerOutputObserver` deliberately ignores only this source because the surrounding repair workflow immediately creates the lineage-preserving request through `request_reverification_after_repair()`. Treating the internal Result/Artifact attachment as a new top-level producer event would otherwise create a second unrelated Verification with repair attempt zero.

The resulting `RepairOutput` is staged durably in the bound reviewer AgentRun telemetry **before** a fresh reverification request is created. If the workflow stops after repair output exists but before `request_reverification_after_repair()` succeeds, a later invocation validates and reuses that exact staged output instead of executing repair again. The staged identity includes the source Verification, repair Run and repair attempt.

The repaired Result/Artifact is then resolved again through canonical evidence, producing a fresh exact Verification subject/revision/digest and incremented repair attempt. An older Verification can never certify modified output. The #86 repair budget remains authoritative and bounds the loop. When the budget is exhausted, a further `NEEDS_CHANGES` result becomes a canonical rejected completion with `verification repair limit exhausted`; no additional repair Run is created.

A successful re-review can leave the Task in `RUNNING` because Step terminalization does not itself complete the parent Task. `AutomaticReviewerOutputCoordinator` therefore asks the normal kernel completion path to finish an accepted Task when it is `WAITING` or `RUNNING` **and** no canonical Run is still active. This also preserves the existing safety rule for Artifacts attached while producer work is still running.

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

The repair path has equivalent recovery boundaries. Canonical Plan/Step/Run creation is idempotent, an already-created queued repair Run can still be started, an active repair Run is reconciled rather than duplicated, the repaired Result/Artifact attachment is idempotent, and staged repair output prevents completed repair work from being executed twice after an interruption.

The process-local serialization matches the current reference AgentRepository write boundary. A future deployment with multiple independent Control Plane writers must provide an equivalent durable uniqueness/claim guarantee at the AgentRepository boundary before enabling concurrent cross-process reviewer dispatch.

## Product/status surface

`ReviewWorkflowResult` exposes the canonical request, bound reviewer AgentRun, VerificationResult, optional canonical `VerificationRepairExecution`, and current `CompletionGateDecision`. `status_for()` reads review state without executing/retrying work.

The existing Verification Control Plane collections remain the public source for canonical request/result/completion state. Normal AgentRun resources expose their `verification_context`, including the exact reviewed subject and `reviewer-selection-v1` provenance, allowing clients to correlate the selected Agent/revision, Team/revision where applicable, reviewer execution, repair attempt and exact Verification without client-private review state. Staged reviewer/repair data in AgentRun telemetry is recovery evidence, not competing lifecycle authority.

## Normal integration example

In the standard durable single-node composition the observer, reviewer and bounded Agent-repair path are already installed. Product code configures the policy/Task requirement and then uses the normal output attachment API:

```python
policy = verification.register_policy(
    VerificationPolicy(
        name="automatic-review",
        stages=(VerificationStage("review", VerifierKind.AGENT),),
        max_repair_attempts=1,
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
# persists the output and drives review plus any permitted bounded repair/re-review loop.
task = await kernel.attach_result(
    idempotency_key=output_command_key,
    task_id=task_id,
    run_id=producer_run_id,
    result_id=result_id,
)
```

If the reviewer passes, the coordinator asks the normal kernel completion path to finish the Task once no canonical Run is active. If the reviewer returns `needs_changes` and repair budget remains, the public reference deployment creates one bounded producer repair Step and a fresh exact Verification. Pending, failed, rejected or escalated review remains blocked according to canonical policy.

## Non-authorities

The following remain explicitly non-authoritative:

- reviewer prose;
- orchestrator-native review state;
- provider session IDs;
- AgentTeam membership by itself;
- reviewer-selection provenance by itself;
- `ReviewerExecutionDecision` and staged recovery copies;
- repair binding metadata and `RepairOutput`;
- the output observer/coordinator return objects.

Only canonical Verification records and `VerificationCompletionAuthority` decide whether required review has passed. Only the kernel and `VerificationRepairRuntime` own canonical repair Plan/Step/Run transitions.
