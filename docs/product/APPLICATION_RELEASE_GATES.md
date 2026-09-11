# Application release gates

Application release gates are a projection owned by application distribution. They do not replace canonical Verification (#86), Evaluation (#19), execution (#7), File/Artifact (#13), or Approval (#15).

The release path is:

`BuildSpecification gate name -> ReleaseGatePolicy -> deterministic/File check or #86/#19 evidence -> GateEvidence -> publication decision`

## Policy configuration

The shipped single-node server can load a versioned local gate catalog through:

```text
AI_MAP_APPLICATION_RELEASE_GATE_POLICY=/path/to/application-release-gates.json
```

The catalog is explicit and secret-free. Schema version `1` maps each gate name to one of:

- `deterministic`: `artifact_exists`, `file_checksum`, or `manifest_checksum`;
- `verification`: exact #86 policy ID/version/stage plus target;
- `evaluation`: exact #19 suite ID/version plus target.

A missing catalog leaves the policy empty. A `BuildSpecification` gate with no configured canonical mechanism therefore does not receive fabricated evidence and publication remains fail-closed unless valid legacy/manual evidence already exists.

Example:

```json
{
  "schema_version": 1,
  "requirements": [
    {
      "name": "checksum",
      "kind": "deterministic",
      "target_id": "linux-x64",
      "deterministic_check": "file_checksum"
    },
    {
      "name": "package-smoke",
      "kind": "verification",
      "target_id": "linux-x64",
      "verification_policy_id": "verification_policy_release",
      "verification_policy_version": 1,
      "verification_stage_id": "package-smoke"
    },
    {
      "name": "regression",
      "kind": "evaluation",
      "target_id": "linux-x64",
      "evaluation_suite_id": "release-suite",
      "evaluation_suite_version": "1"
    }
  ]
}
```

The referenced #86 policy and #19 suite are still registered and governed by their canonical owners. The release-gate catalog only selects them.

## Deterministic evidence

`artifact_exists` requires the exact target Artifact to exist. `file_checksum` delegates checksum verification to the canonical FileProvider.

`manifest_checksum` validates the deterministic release manifest against the versioned canonical schema in `docs/schemas/application-release-manifest.schema.json`, then records a SHA-256 of the canonical manifest. Because GateEvidence is itself part of that manifest, the checksum excludes only the checksum gate's own entry; all other release inputs and gates remain covered. A regression test requires the runtime schema mirror to stay exactly equal to the documented schema.

## Package smoke checks

Application distribution does not execute a package smoke command itself. Doing so would create a second test/execution authority inside the release domain.

A package smoke requirement is configured as a #86 `verification` gate whose versioned Verification stage is deterministic and may name the execution capability responsible for the smoke check. The verifier performs the configured smoke through the normal execution/capability boundary and records the result in canonical Verification. Application distribution then projects only the exact-subject `VerificationRequest`/`VerificationResult` references and outcome into `GateEvidence`.

This keeps responsibilities separate:

- #7/Executor or the selected capability owns command execution;
- #86 owns verifier identity, policy, exact subject, immutable result and outcome;
- application distribution owns whether that named passing result is required before publication.

No LLM or paid external service is required for deterministic package smoke verification.

## Exact subject and recovery

Release evidence is bound to source revision, Workspace snapshot/checksum, BuildSpecification ID/revision, target Artifact/File identity and Artifact SHA-256. A changed source, build specification or Artifact therefore requires fresh evidence.

Verification reconciliation uses canonical #86 history to recover an exact request/result after process restart or loss of the derived GateEvidence projection. It reuses the existing request instead of creating a duplicate. Conflicting terminal outcomes for the same exact subject are surfaced as `inconclusive` and block publication.

For a configured #19 evaluation gate, reconciliation first reuses any canonical run whose suite ID/version and `application_release_artifact` reference exactly match the current Artifact ID, SHA-256 and source/build/target revision. If no such run exists, the gate coordinator asks the canonical `EvaluationService` to execute that exact suite version with the exact release Artifact reference in its immutable configuration snapshot. Application distribution never constructs an `EvaluationRun` or `EvaluationResult` itself. Concurrent retries through one coordinator are serialized, and restart reconciliation sees the persisted canonical run before deciding whether another launch is needed.

Evaluation projection then accepts only those exact-subject #19 runs. Missing, unavailable, pending, failed, inconclusive or mismatched mandatory evidence blocks publication. A changed source, BuildSpecification revision or Artifact produces a different subject and therefore requires a fresh run. Evaluation remains optional when the BuildSpecification does not require its gate name.

## Control Plane and publication

Application-release resources expose derived `release_gate_state`, including required gates, current subject, evidence references, blocking reasons and `publication_permitted`. Clients do not need to query verifier/evaluator internals to determine release readiness.

Publication remains fail-closed. Approval is still a separate authorization decision and never substitutes for release Verification.
