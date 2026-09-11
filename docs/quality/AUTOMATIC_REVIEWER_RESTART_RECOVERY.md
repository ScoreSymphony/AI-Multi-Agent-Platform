# Automatic reviewer restart recovery

Issue #758 hardens the automatic reviewer-Agent workflow from #711 for ordinary Control Plane process loss. It does **not** add a reviewer-specific lifecycle authority. Canonical Verification remains the only review/completion authority, and canonical Task/Plan/Step/Run state remains owned by the kernel.

## Scope and authority

The durable inputs used during reviewer recovery are:

- canonical `VerificationRequest` / `VerificationResult` state;
- ordinary `AgentRunRecord` history, including immutable `verification_context` correlation evidence;
- staged reviewer decisions and staged repair output already stored in AgentRun telemetry;
- canonical Task state for cancellation checks;
- canonical repair Plan/Step/Run/Result lineage owned by the existing repair runtime.

Recovery never derives `PASS`, `FAIL`, or `NEEDS_CHANGES` from an AgentRun status. An AgentRun can prove that work existed, failed, was cancelled, or staged recoverable output; only canonical Verification can accept review output.

## Single-node ownership rule

There are two deliberately different execution contexts:

1. **Normal in-process workflow execution.** `AutomaticReviewerWorkflow` treats an existing unstaged `RUNNING` reviewer AgentRun as live/in-flight and does not dispatch another reviewer. This prevents duplicate live reviewer execution while the current Control Plane still owns the process-local work.
2. **Ordinary single-node startup after process loss.** `AutomaticReviewerStartupReconciler` runs before serving. In this profile the previous Control Plane process is known to be gone, so an unstaged reviewer AgentRun left `RUNNING` by that previous process is explicit abandoned-process evidence. Recovery terminalizes that stale AgentRun as failed and then re-enters the existing bounded reviewer workflow for at most the remaining retry budget.

This process-loss inference is valid only for the single-writer/single-Control-Plane startup profile. It is **not** a distributed liveness protocol. A deployment with concurrent independent Control Plane writers must provide a durable claim/lease or equivalent ownership proof before it can classify another writer's `RUNNING` reviewer as abandoned. Process-local locks are insufficient for that case.

If startup encounters conflicting evidence, such as more than one running reviewer AgentRun for the same Verification, it fails closed and records an explicit blocker instead of guessing ownership.

## Startup classification

For every durable Agent-verifier request, startup reconciliation classifies and handles the current evidence as follows:

| Durable state | Startup action |
| --- | --- |
| Pending Verification, no reviewer AgentRun | Re-enter the existing reviewer workflow and dispatch exactly one bounded attempt. |
| Pending Verification, prior failed/cancelled reviewer | Re-enter the existing workflow; its existing retry budget decides whether another attempt is allowed. |
| Pending Verification, succeeded/running reviewer with staged decision | Reuse the staged decision and resume canonical submission without another model call. |
| Pending Verification, unstaged `RUNNING` reviewer from the previous single-node process | Mark the stale run failed with recovery telemetry, then permit one bounded normal retry if budget remains. |
| Multiple running reviewer AgentRuns | Fail closed with a startup blocker. |
| Cancelled/expired Verification | Cancel any stale running reviewer and do not reopen review. |
| Cancelled canonical Task | Cancel a pending Verification and stale running reviewer; late reviewer completion cannot release the Task. |
| Completed Verification | Reuse the immutable canonical result. A stale running reviewer is cancelled; no new review is invented unless the completed result canonically requires bounded repair/re-review. |
| Changed canonical subject revision/digest | Canonical evidence validation rejects stale submission; startup records a blocker and does not certify the modified output. |

The reconciliation result is written into the ordinary single-node startup report. It records the Verification ID, Task ID, reviewer AgentRun IDs involved, recovery disposition, replacement attempt where applicable, and blocker reason without creating a second reviewer log database.

## Staged reviewer decision boundary

The normal reviewer workflow persists the structured reviewer decision into AgentRun telemetry before submitting the canonical VerificationResult. Therefore a crash after model execution but before Verification submission is recoverable:

```text
reviewer model execution
  -> staged automatic_reviewer_decision
  -> process loss
  -> startup reconciliation
  -> exact binding + current canonical subject validation
  -> canonical Verification submission
```

The model is not called again. If the subject revision/digest changed while the process was down, canonical evidence validation fails closed.

## Cancellation and delayed callbacks

Recovery does not make an old reviewer callback authoritative. When a reviewer is terminalized as abandoned/cancelled, a later callback for that old AgentRun is rejected by the ordinary reviewer bridge because the run is no longer a successful live completion candidate. If a replacement attempt has already completed canonical Verification, the earlier callback still cannot overwrite or duplicate that result.

Task cancellation is checked from canonical Task state before reviewer recovery proceeds. A pending Verification is cancelled and stale running reviewer work is terminalized as cancelled. Re-running startup reconciliation remains idempotent.

## Repair and re-review boundaries

A completed `NEEDS_CHANGES` Verification is deliberately re-entered through `AutomaticReviewerWorkflow`; startup does not create a separate repair state machine. The existing #711/#86 recovery points therefore remain authoritative:

1. **Before repair Run creation:** `VerificationRepairRuntime.start_repair()` performs the canonical bounded repair decision and creates the repair Plan/Step/Run through ordinary kernel commands.
2. **After repair Run creation but before execution:** the repair runtime finds the existing canonical run by its deterministic repair causation/idempotency identity. `KernelAgentRepairExecutor` starts/reconciles that existing run rather than creating another.
3. **During active repair execution:** the existing canonical Run is refreshed/reconciled through the kernel. Recovery does not create a parallel repair lifecycle.
4. **After successful repair execution but before Result attachment:** the repair executor attaches the same canonical Result with the deterministic repair idempotency key.
5. **After repaired output is staged but before fresh VerificationRequest:** `automatic_reviewer_repair_output` on the original reviewer AgentRun is validated against the exact source Verification, repair Run and repair attempt, then reused. Successful repair is not executed again.
6. **After fresh VerificationRequest but before reviewer dispatch:** `request_reverification_after_repair()` reuses an exact existing descendant request when one already matches the repaired canonical subject/lineage; the reviewer workflow then handles the pending request normally.
7. **During repaired-output review:** the same reviewer restart rules apply to the descendant Verification. Retry and repair attempt budgets remain bounded by their existing policy fields.

Repeated startup passes are therefore expected to converge on the same canonical Plan/Step/Run/Result/Verification lineage rather than creating duplicates.

## Startup ordering and readiness

Normal durable single-node startup performs canonical Run recovery first. Reviewer reconciliation runs only after unresolved canonical Runs have been cleared because reviewer/repair recovery must not race unknown execution ownership.

A reviewer reconciliation record with disposition `blocked` is added to `blocked_verification_ids` and makes startup `ready_for_service = false`. The server refuses to enter normal serving while such reviewer blockers remain. This gives operators an explicit durable report instead of silently leaving review work waiting forever.

## Local-first behavior

The restart path uses the existing local durable repositories, kernel recovery, Agent runtime and provider-neutral reviewer composition. It does not require a hosted recovery service or paid model/API provider. Local/self-hosted model configurations continue to use the same recovery semantics.

## Multi-process boundary

The reference implementation intentionally stops at the current single-node writer boundary. A future multi-writer deployment must add a durable uniqueness/claim mechanism that can prove one of the following before acting on a `RUNNING` reviewer:

- a specific writer still owns the live execution;
- ownership has expired/been relinquished and a new writer may recover it;
- ownership evidence is conflicting, in which case review remains blocked.

Until such a mechanism exists, independent processes must not infer abandonment merely from their own process-local lock state.
