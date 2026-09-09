# Issue #694 — Learning source evidence hardening

## Status

This follow-up hardens the governed Learning pipeline completed by #595. It does not widen Learning mutation authority and it does not introduce a second source of truth for Task/Run, Planning or Research evidence.

## Authority model

Learning stores only exact references projected from canonical owner domains. Source authenticity is resolved before a `LearningCandidate` is created:

- Run failure evidence is read from the platform-owned event-sourced Kernel.
- Planning/replanning evidence is read from durable `PlanningService` proposal history.
- Research evidence is read from the canonical Research repository and its full item/claim/source/observation/evidence ownership chain is checked.
- Operator proposals remain explicitly operator-authored and cannot claim trusted system-source reference kinds through `LearningSourceBridge.operator_proposal(...)`.

The resulting Candidate remains a proposal. Existing Evaluation/Verification, authorization, Approval, stale-target and owner-domain promotion gates are unchanged.

## Run failure evidence

`KernelRunFailureEvidenceResolver` accepts a narrow `RunFailureSourceRef` lookup identity and re-reads the canonical `TaskState` and `RunState`.

A Run can contribute to a repeated failure pattern only when:

1. the canonical Run resolves under the requested Task;
2. the Run is `failed` or `timed_out`;
3. Task and Run agree on project ownership;
4. the Candidate project matches the canonical Run project;
5. an optional supplied Run revision matches the current canonical projection revision;
6. an optional supplied digest matches the deterministic canonical Run projection digest.

The resolver emits an exact `run_failure` Learning reference with revision and digest plus a supporting Task revision reference. A mixed valid/invalid pattern fails as a whole. Duplicate canonical references do not count toward the two-observation minimum.

## Planning/replanning evidence

`PlanningProposalFailureEvidenceResolver` re-reads `PlanningService.history(task_id)` and the canonical Task project before projecting a Learning reference.

Qualifying evidence is either:

- a proposal created from a failure/replanning trigger such as terminal failure, retry exhaustion, Verification failure/changes-required/inconclusive, unavailable Agent/Capability/Model, invalidated assumption or feasibility blocker; or
- an invalid Planning proposal record.

Manual/ordinary proposals do not become failure evidence merely because a caller labels them that way. Optional supplied proposal revision/digest values are checked against the durable `ProposalRecord` and `PlanProposal.digest`. Project mismatches fail closed.

## Research evidence

`LearningSourceBridge.from_research_evidence(...)` now verifies the complete canonical ownership chain before candidate creation:

- Evidence -> Research Item;
- Evidence -> Claim;
- Evidence -> Source;
- Evidence -> Source Observation;
- Claim -> Research Item and Evidence membership;
- Source -> Research Item and Observation membership;
- Research Item -> Claim, Source and Evidence membership;
- Source Observation -> Research Item and Source.

Candidate evidence preserves the Research Item and Claim revisions/digests, the Evidence digest, the Source identity and the best available Source Observation revision/digest. An explicitly supplied Candidate project must match the canonical Research Item project.

## Operator boundary

`LearningSourceBridge.operator_proposal(...)` rejects source references that use trusted canonical system-source kinds such as Verification, Evaluation, Run failure, Planning failure or Research Evidence identities. Those categories must be created through the corresponding canonical bridge.

The public `learning.propose` command remains an operator-proposal command: the server selects `LearningSourceType.OPERATOR_PROPOSAL`; a caller-supplied `source_type` does not select a trusted system-derived category.

## Runtime composition

The durable Single-Node deployment wires Learning to the existing canonical owners:

- `base.kernel` -> `KernelRunFailureEvidenceResolver`;
- `planning` -> `PlanningProposalFailureEvidenceResolver`;
- `context.research` -> Research source bridge.

No Learning-private Run or Planning store is added.

Automatic Candidate generation is **not enabled by this hardening**. Candidate generation remains an explicit invocation of the source bridge. This is deliberate: #694 makes the evidence trustworthy first, while the #595 invariant remains that automatic Candidate generation, if later enabled by deployment policy, must never imply automatic acceptance or promotion.

## Regression coverage

`tests/test_issue_694_learning_source_evidence.py` covers:

- two canonical Run failures -> Candidate;
- duplicate Run evidence rejected as a repeated pattern;
- missing, successful/non-failure, stale revision/digest and cross-project Run evidence rejection;
- mixed valid/invalid Run pattern fail-closed behavior;
- two canonical Planning failure/replanning records -> Candidate;
- duplicate, missing, non-failure, stale revision/digest and cross-project Planning evidence rejection;
- exact Research ownership/revision/digest projection;
- broken Research ownership chain and project mismatch rejection;
- operator-source impersonation rejection;
- public `learning.propose` source-type boundary.

`tests/test_issue_694_source_immutability.py` additionally proves that resolving/projecting canonical Run, Planning and Research evidence leaves the historical owner records unchanged.

Existing #595 tests continue to prove quality gates, authorization/Approval, stale target handling, restart-safe owner revision promotion and historical Feedback/Verification/Evaluation immutability.

## Non-goals retained

This work does not:

- enable unrestricted automatic promotion;
- add Learning-private copies of canonical Task/Run/Planning/Research state;
- expand owner-domain promotion support beyond the #595 V1 owners;
- convert every failure or chat message into durable Learning;
- weaken Evaluation, Verification, authorization, Approval, project-scope or stale-target checks;
- require a paid external model/API service.
