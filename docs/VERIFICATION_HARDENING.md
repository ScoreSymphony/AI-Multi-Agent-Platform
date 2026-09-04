# Canonical Verification hardening

This document records the post-#224 hardening required to make issue #86 effective in the production-shaped reference composition, not only in isolated Verification tests.

## Standard single-node composition

`build_single_node_deployment()` composes Verification as a normal durable platform service:

- `SqliteVerificationService` stores policies, requests, immutable results and audit history in `verification.sqlite3`;
- `SqliteVerificationCompletionAuthority` stores Task-to-policy/exact-subject requirements in the same durable Verification database namespace;
- `PlatformKernel` receives that completion authority, so every task-level success path is subject to the canonical Verification gate;
- the Verification Control Plane collections and human-review commands are registered on the existing #32 extension seam;
- `VerificationTimelineReader` is bound to the existing #16 timeline projection;
- `CanonicalVerificationRuntime` and `KernelFileVerificationEvidenceResolver` are exposed by the deployment for safe request creation.

Tasks with no Verification requirement preserve the existing success behavior. Merely enabling the completion authority does not force review globally.

## Canonical subject resolution

Callers should not invent a `VerificationSubject` revision or digest. The production-shaped request path is `CanonicalVerificationRuntime`, which accepts a canonical Result or Artifact ID and derives the exact subject through a replaceable `VerificationEvidenceResolver`.

The reference `KernelFileVerificationEvidenceResolver` resolves:

- **Result**: the Result must be attached to a canonical terminal Run. The subject revision is bound to the exact Run ID and attempt, and its SHA-256 digest is derived deterministically from the canonical Run outcome/output projection and attached Artifact IDs.
- **Artifact**: the Artifact must be attached to the Task and resolve to exactly one READY canonical `FileRecord`. The file checksum is reverified before the subject is returned. The subject revision is the canonical File ID and the digest is the FileRecord SHA-256.

The resolver fails closed when the platform cannot prove a unique exact subject. In particular, the reference Result resolver does not pretend that a task-only Result attachment has content identity when no canonical Result content projection exists.

Human review handlers and reviewer-Agent startup can receive the same resolver. When configured, they re-resolve the current canonical subject before accepting reviewer evidence, preventing an older or caller-forged revision/digest from being reviewed as if it were current.

Evidence Artifact IDs submitted by an authorized human are also resolved through the canonical evidence resolver. Syntactically valid but unknown, unattached, ambiguous, non-ready or checksum-invalid evidence is rejected instead of being recorded as trustworthy provenance.

## Independence is fail-closed

Policy-selected reviewer independence is now proof-based. If a policy requires an independent producer/reviewer Agent, model or provider and the relevant producer or reviewer identity is unavailable, Verification returns `FORBIDDEN` instead of silently skipping the rule.

`ReviewerIndependence` additionally supports:

- `human_reviewer_must_differ`: a human reviewer cannot have the same canonical actor reference as the producer;
- `forbid_self_verification`: rejects same-actor or same-Agent self-verification independently of the narrower Agent/model/provider rules;
- `require_distinct_verifiers`: together with a stage `minimum_results > 1`, only distinct verifier identities count toward the required reviewer quorum.

Read-only Agent enforcement remains capability-based and fail-closed where the side-effect classification cannot be proven.

## Timeout and cancellation lifecycle

A policy may select `timeout_failure_policy` independently from ordinary verifier failure handling:

- `wait` -> completion remains `waiting`;
- `fail` -> completion becomes `rejected`;
- `escalate` -> completion becomes `escalated`.

Expired requests therefore have an explicit deterministic completion-policy effect instead of being indistinguishable from a review that was never requested.

`VerificationService.cancel_request()` implements the previously modeled `cancelled` request state. Cancellation is allowed only from `pending`, is retry-idempotent once cancelled, prevents later result submission, emits `verification.request_cancelled`, and is persisted by `SqliteVerificationService` across restart.

## Lifecycle authority remains unchanged

These hardenings do not move Task lifecycle ownership into Verification. Verification derives and records evidence and returns deterministic completion decisions. `PlatformKernel` remains the only canonical component that emits Task lifecycle transitions.

A rejected Verification continues to block the Task as canonical `WAITING` with `verification:rejected`; it does not rewrite a successful execution Run as failed. This preserves the established distinction between execution truth and acceptance truth. A later policy or product decision may introduce a separate terminal acceptance transition, but #86 does not silently reinterpret `RunStatus.SUCCEEDED`.

## Regression coverage

The hardening regression suite covers:

- fail-closed Agent/model/provider/human independence when required identity is missing;
- separate-human and generic no-self Verification;
- N-reviewer quorum with distinct verifier identities;
- explicit timeout `wait` / `fail` / `escalate` behavior;
- in-memory and durable SQLite request cancellation;
- exact Result subjects derived from canonical terminal Run output;
- exact file-backed Artifact subjects and checksum tamper detection;
- Control Plane rejection of a forged subject and unknown Evidence Artifact;
- real single-node Task completion blocking, human acceptance and durable restart recovery.

## Authority-integrity hardening

The production-shaped composition now runs `SqliteVerificationService` in strict canonical-subject mode. Raw caller-supplied `VerificationSubject` requests are rejected there; `CanonicalVerificationRuntime` is the supported request boundary and obtains an internal canonical-subject permit only after evidence resolution. The low-level provider-neutral service remains permissive by default for isolated adapters/tests.

`VerificationEvidenceContext` derives Task project scope and, when a canonical AgentRun produced the reviewed Result/Artifact, the exact producer Agent revision, selected model/provider and capability IDs. Canonical request creation no longer accepts those facts from callers. Reverification derives the Task from the previous immutable request and resolves producer context again for the repaired revision, preventing cross-Task rebinding and stale producer provenance.

Repair execution uses a stable key `(source verification ID, repair attempt)`. A caller retry with a different idempotency key therefore reuses the same canonical Plan/Run; durable kernel history is checked before any new repair mutation.

Completion assessment scans every stage for critical failure before returning an earlier incomplete-stage `waiting` result. Distinct Agent reviewer quorum is keyed by Agent ID rather than revision-specific verifier text, so two revisions of one Agent do not count as two independent reviewers.

## Integration validation discipline

Because Verification is a cross-cutting completion authority, a green feature-branch test run is not the final integration signal after `main` advances. The pull-request merge-ref CI must be rerun against the then-current `main`; mergeability alone proves only that Git can compose the histories, not that the combined platform behavior still passes the repository-wide gates.

## Final submission attestation

The production-shaped Verification service now requires both canonical request subjects and canonical result submissions. `CanonicalVerificationRuntime.submit_result()` re-resolves the current Result/Artifact subject and validates every evidence Artifact immediately before the immutable `VerificationResult` is recorded. A review that started on revision V1 therefore cannot certify V1 after canonical output has advanced to V2, and direct raw `submit_result()` calls fail closed in strict mode.

Kernel `result.attached` and `artifact.attached` mutations notify completion authorities that opt into the output-change hook. Verification responds by clearing only the current Task→subject completion binding; historical requests/results remain immutable. A new exact subject must be canonically requested before Task completion can become accepted again.

`VerificationPolicy.risk_classification` uses the existing #15 `RiskClassification` vocabulary. `ReviewerIndependence.forbid_self_verification_risk_classes` can forbid self-verification only for selected policy risk classes while preserving lower-risk policy behavior.

Reviewer Teams are coordination context, not a new lifecycle authority. `ReviewerAgentRuntime.start_review()` can pin a concrete reviewer Agent to an exact Agent Team revision; membership and member revision are validated before execution, the resulting `AgentRunRecord.team` preserves Team provenance, and the canonical `VerificationResult` remains attributable to the concrete reviewer Agent.
