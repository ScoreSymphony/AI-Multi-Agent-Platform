# Runtime Verification and Review

Issue #86 introduces a platform-owned verification layer for deciding whether one concrete Task result is acceptable before final completion.

## Boundary

Verification is intentionally distinct from two existing concerns:

- **Authorization / Approval (#15):** whether a proposed sensitive action may execute.
- **Evaluation (#19):** repeatable quality/regression measurement across configurations.
- **Verification (#86):** whether the exact Result/Artifact revision produced by one runtime Task/Run satisfies its completion policy.

Approval never certifies output quality. Verification never grants permission for a later privileged action.

## Canonical records

The canonical surface lives in `ai_multi_agent_platform.verification` and defines:

- `VerificationPolicy`: versioned stages, scopes, outcome requirements, independence rules, repair limits, timeout/expiry behavior and failure policy;
- `VerificationSubject`: exact `result` or `artifact` ID plus revision and digest;
- `VerificationRequest`: one concrete obligation bound to a policy version, stage, Task/Run and exact subject;
- `VerificationResult`: immutable verifier identity/type, outcome, findings, evidence, checks, errors and exact reviewed subject;
- `CompletionAssessment`: deterministic platform decision of `accepted`, `waiting`, `repair_required`, `rejected` or `escalated`;
- `TaskVerificationRequirement`: the Task-to-policy and exact-subject binding consumed by the runtime completion gate;
- `CompletionGateDecision`: the kernel-facing completion decision;
- `CompletionAuthority`: the replaceable protocol used by the canonical kernel;
- `VerificationCompletionAuthority`: the reference implementation backed by `VerificationService`;
- `VerificationAuditEvent`: append-only, content-safe audit fact for policy/request/result activity.

The original Verification history is retained when a repair produces a new subject revision. A passed result for revision A is not considered evidence for revision B.

## Verifier types

The canonical model supports:

- deterministic checks;
- human reviewers;
- reviewer Agents;
- replaceable external/domain verification providers.

`ReferenceDeterministicVerifier` is local and LLM-free. It only records a pass if all configured deterministic checks pass.

Reviewer Agents are ordinary Agents. Recording a reviewer-Agent result does not mutate Task lifecycle state and does not grant merge, deployment, administrative or approval authority.

## Independence

Policies may require any combination of:

- reviewer Agent differs from producer Agent;
- reviewer model differs from producer model;
- reviewer provider differs from producer provider;
- Agent reviewer is read-only;
- multiple accepted results come from distinct verifier identities.

These restrictions are opt-in policy rules, not global assumptions.

## Completion policy

`VerificationService.assess_completion()` evaluates only results that match all of:

- Task ID;
- policy ID and exact policy version;
- verification stage;
- exact `VerificationSubject` including revision and digest;
- non-expired result window where configured.

A required stage that has not produced enough accepted results remains `waiting`. A critical failure follows the policy failure mode. `needs_changes` returns `repair_required` only while repair budget remains.

The service deliberately does **not** expose a method that marks a Task succeeded. The canonical kernel remains lifecycle authority.

## Kernel completion gate

`PlatformKernel` accepts an optional `CompletionAuthority`. When no authority is configured, existing Task/Run completion behavior remains unchanged. When one is configured, every task-level success path is checked before the kernel emits `task.succeeded`.

This covers both canonical success paths:

1. direct `complete_task()` calls;
2. automatic Task completion caused by a successful task-level Run.

A successful Run is still recorded as `run.succeeded`. If the Task has a required Verification that is not currently accepted, the Task is instead moved to canonical `WAITING` state with a `verification:<state>` wait reason and `blocked=true`. This keeps execution truth separate from completion acceptance: a Run may have executed successfully while the Task still requires review.

Once the exact bound subject satisfies its policy, `complete_task()` may finalize a Verification-waiting Task. The kernel first emits `task.resumed` and then `task.succeeded`, preserving the existing lifecycle state machine rather than adding a parallel review-only Task status.

A changed Result/Artifact revision or digest is rebound through `VerificationCompletionAuthority`. Previous accepted Verification results therefore cannot authorize completion of a newer subject.

The kernel remains the only component that mutates canonical Task lifecycle state. Orchestrators, reviewer Agents and Verification providers can contribute evidence or completion decisions, but cannot independently emit a successful Task transition.

## Durable persistence and recovery

The replaceable in-memory semantics are complemented by SQLite reference implementations:

- `SqliteVerificationService` persists versioned policies, requests, request status changes, immutable results and Verification audit history;
- `SqliteVerificationCompletionAuthority` persists the Task-to-policy and exact-subject completion requirement.

Both can use the same local SQLite database while retaining separate namespaces for service evidence and Task completion bindings. The persisted document format is explicitly versioned by `VERIFICATION_PERSISTENCE_SCHEMA_VERSION`.

On restart, the durable service reconstructs policy/request/result/audit state and rebuilds its request-to-result index. Restore validates cross-record consistency rather than trusting serialized data blindly: referenced policy versions and stages must exist, verifier kinds must still match their requests, completed requests must have exactly one result, audit events must reference existing canonical records, and every restored result must bind to the exact subject originally requested. Unsupported or internally inconsistent persisted state fails closed with a canonical backend error.

Request expiry is persisted when observed, so a request that became `expired` does not return to `pending` after process restart. Repair attempts and prior Verification results remain durable history across result revisions. Restore does not re-emit historical audit events.

Persistence also protects the crash window between durable Verification evidence and the Task completion binding. Service requests are retained in durable creation order, and the requirement snapshot records how much of that request stream it had incorporated. If a process stops after a newer VerificationRequest was stored but before its new subject was bound to the Task requirement, restart reconciliation replays the durable request tail and advances the Task binding to the newer exact subject before completion can be assessed. A previously accepted older revision therefore cannot be revived by this partial-write window. If requirement state is ahead of service evidence, or a recovered tail conflicts with the Task's canonical policy/version, recovery fails closed instead of guessing.

SQLite is a reference persistence implementation, not a semantic dependency. The canonical Verification and CompletionAuthority contracts remain provider-neutral so another durable backend can replace SQLite without changing completion-policy behavior.

## Control Plane and human review

`ai_multi_agent_platform.verification.control_plane.register_verification_control_plane(...)` attaches #86 to the generic registration seam owned by #32 instead of creating a second router or API authority. It registers three read collections:

- `verifications`: canonical request/result history;
- `verification-reviews`: pending human-review queue;
- `verification-requirements`: Task policy/subject binding plus current completion decision.

It also registers the canonical human-review commands:

- `verification.accept` → `pass`;
- `verification.reject` → `fail`;
- `verification.request-changes` → `needs_changes`.

The existing Control Plane exposes these registrations through its normal `/api/v1/...` collection routes, generic `/api/v1/commands/...` command route and OpenAPI extension metadata. Verification therefore does not duplicate HTTP, versioning or OpenAPI machinery.

Generic collection authorization is followed by object-scoped authorization against the canonical owning Task. Task owner type/ID and project ID are propagated to the existing #15 authorization provider. Human-review mutations also bind the authorization request to a digest of the submitted command payload. Unauthorized Task resources are filtered from list/queue results and fail closed on direct read or mutation.

The authenticated Control Plane principal becomes the canonical human verifier identity. Optional review comments become structured findings, and submitted evidence Artifact IDs are preserved on the `VerificationResult`. Human reviewers remain read-only verification actors; recording an accepted result does not itself grant merge/deploy/admin authority.

Human-review commands are retry-safe. The recorded result stores the Control Plane idempotency key, payload digest, actor and action in namespaced metadata. Repeating the same logical command returns the existing canonical result; attempting to replay a completed request with a different payload/outcome conflicts instead of silently rewriting review history.

The Control Plane still does **not** mutate Task completion directly. It records the authorized canonical VerificationResult; `PlatformKernel` remains the sole lifecycle authority and subsequently consults `CompletionAuthority` before success.

The Control Plane extension is deliberately imported from the `verification.control_plane` submodule rather than re-exported from `verification.__init__`. The kernel imports the Verification package itself, so a top-level re-export would create an avoidable `verification -> control_plane -> kernel -> verification` import cycle.

## Canonical audit and #16 observability

`VerificationService` appends canonical `VerificationAuditEvent` records for policy registration, initial/reverification requests, observed request expiry and recorded results. These events preserve IDs, exact subject digest/revision, policy/stage, verifier classification, outcome, evidence references and trace context while deliberately excluding human comment text, finding prose and Artifact bodies.

`SqliteVerificationService` persists this audit sequence together with Verification state and validates it on restore. Audit history is therefore restart-safe evidence, not a transient telemetry buffer.

`VerificationTimelineReader` in `ai_multi_agent_platform.verification.observability` projects canonical audit facts into the existing #16 Control Plane timeline seam. The projection is one-way: #16 telemetry/timeline data never feeds completion assessment and is never a substitute for canonical Verification state. Replacing an exporter or observability backend therefore cannot alter acceptance semantics.

The audit and observability submodules are intentionally not made lifecycle authorities. They can expose what happened; only Verification policy evaluates acceptance, and only `PlatformKernel` mutates Task lifecycle state.

## Reviewer-Agent runtime

`ai_multi_agent_platform.verification.reviewer_agent.ReviewerAgentRuntime` binds one pending Agent verification request to the existing #33 `AgentRuntime`. A reviewer is therefore an ordinary versioned Agent execution rather than a special private review process.

Before an AgentRun is created, the runtime resolves the exact Agent revision, selected model/provider and selected capability versions, constructs the canonical `VerifierIdentity`, and calls `VerificationService.validate_verifier()`. Independence rules are therefore enforced before reviewer execution starts rather than only when its result is submitted.

The exact review obligation is snapshotted into `AgentRunRecord.verification_context`, including Verification ID, policy/stage, exact subject type/ID/revision/digest and the resolved reviewer identity. Once non-empty, this verification context cannot be rewritten when the AgentRun is finished. Caller-supplied Task context also cannot override the reserved canonical `verification` context.

When policy requires a read-only reviewer, every selected capability must be provably side-effect-free. With a canonical CapabilityRegistry attached, the reviewer runtime resolves each selected capability and requires `SideEffectClassification.NONE`. If read-only safety cannot be established, reviewer start fails closed. This is a Verification independence check, not a replacement for #15: actual capability use remains subject to normal authorization/approval enforcement.

Only a successfully finished, still-canonically-bound AgentRun may submit a reviewer result. `complete_review()` converts that execution identity into the canonical `VerificationResult` through `VerificationService.record_agent_review()`. The reviewer runtime never calls `complete_task()` and cannot turn a waiting Task into succeeded state; the kernel must still re-evaluate completion separately.

The reviewer runtime is imported explicitly from its submodule rather than from `verification.__init__`, because it depends on the Agent runtime and a top-level re-export would introduce avoidable package cycles.

## Canonical repair loop

`request_reverification_after_repair()` still owns the Verification-side transition and requires:

1. an existing completed `needs_changes` result;
2. remaining policy repair budget;
3. a new exact subject revision or digest.

`ai_multi_agent_platform.verification.repair.VerificationRepairRuntime` supplies the runtime-side bridge from a current `repair_required` decision into ordinary canonical kernel execution. It owns no private Task/Step/Run state.

A repair may start only when the referenced `needs_changes` result is one of the current completion decision's blocking Verification IDs and still matches the exact bound subject. The policy's `max_repair_attempts` remains the authoritative repair budget. A waiting Task must be canonically blocked with `verification:repair_required` before the bridge resumes it.

The reference repair flow is:

1. `PlatformKernel.plan_task()` creates a new canonical Plan/Step set using the replaceable orchestrator;
2. the Verification-waiting Task is resumed through the normal kernel lifecycle;
3. `PlatformKernel.create_run(subject_type="step")` creates the repair Run for a canonical Step;
4. `PlatformKernel.start_run()` dispatches that Run through the normal replaceable lifecycle backend;
5. the repair produces a new Result/Artifact revision through normal runtime mechanisms;
6. `request_reverification_after_repair()` creates a new VerificationRequest, increments the repair cycle, and rebinds completion to the new exact subject;
7. the old request/result remain immutable history.

All repair kernel mutations use `verification-repair` provenance and causation keys derived from the source Verification ID/repair cycle, so the repair is visible in normal canonical Task/Run history without a second audit store.

The bridge deliberately does not become a scheduler. If the replaceable orchestrator proposes exactly one repair Step, the reference runtime can start it directly. If multiple Steps are proposed, an explicit canonical `step_id` is required; the bridge refuses to invent ordering/selection policy. Kernel Run retry/attempt semantics remain distinct from Verification repair cycles.

Starting the repair Run does not itself consume acceptance or create a new Verification result. Completion remains blocked until a genuinely new exact Result/Artifact revision is produced and verified. Once the configured repair budget is exhausted, no new repair Run can be started through this path and the configured failure/escalation policy remains visible.

Like the reviewer bridge, the repair runtime is imported from its explicit submodule rather than top-level `verification.__init__`, because it depends on `PlatformKernel`.

## Security

Verification records are not security approvals. Reviewer Artifact/Workspace access and all repair side effects must still pass the normal #15 authorization/approval boundary.

A policy-level `read_only` reviewer requirement is enforced by reviewer-runtime preflight when selected capability safety can be proven from canonical capability definitions. It does not grant access, bypass capability authorization or turn a verification pass into permission for a later privileged action.

## Current implementation slice

The current #86 implementation provides:

- canonical models and exact subject binding;
- policy registration and scoped request creation;
- deterministic, human and reviewer-Agent result recording;
- reviewer-independence checks;
- completion assessment;
- bounded reverification after repair;
- immutable per-Task review history;
- Task-to-policy/subject completion requirements;
- kernel completion authority integration;
- anti-bypass gating for both direct Task completion and successful task-level Run completion;
- canonical `WAITING` semantics while review is incomplete, repair-required or rejected;
- backward-compatible behavior when no Verification requirement exists;
- versioned SQLite persistence for policy/request/result/audit and completion-requirement state;
- restart restoration of request status, immutable history, exact subjects, audit and completion decisions;
- fail-closed persisted-state validation and crash-window reconciliation;
- registered Control Plane read/history/review surfaces;
- authorized human accept/reject/request-changes commands with Task owner/project scope and payload digest binding;
- retry-safe human review identity/evidence/history behavior;
- canonical content-safe Verification audit history and #16 timeline projection;
- reviewer-Agent runtime binding with pre-start independence/read-only enforcement and immutable exact-subject context;
- canonical bounded repair execution through ordinary Plan/Step/Run kernel operations;
- end-to-end repair-to-new-subject-to-reverification coverage;
- focused regression tests for core, kernel-gate, persistence/recovery, Control Plane authorization, observability, reviewer-Agent and repair semantics.

Remaining issue work is intentionally layered on top of these authorities:

- frontend pending-review/detail/history/action surface;
- broader replacement conformance tests proving equivalent completion semantics across replaceable orchestrators/models/providers.
