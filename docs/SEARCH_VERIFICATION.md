# Verification discovery in global Search (#291)

The Verification integration layers on the canonical Search foundation from #45 and the canonical Verification authority from #86.

## Searchable resources

Global Search derives safe documents for:

- versioned Verification Policies (`verification_policy`);
- Verification Requests together with the presence and safe outcome metadata of their canonical Verification Result (`verification`);
- task-scoped Verification Requirements (`verification_requirement`).

The pending human-review queue is deliberately not indexed because it is a filtered navigation view over the same canonical Verification Requests and would create a second Search identity.

## Authority boundary

Search is discovery-only. It cannot certify a result, accept a task, or replace `VerificationCompletionAuthority`. Search documents are rebuilt from canonical Verification resources and can be discarded and reconstructed.

## Privacy and evidence boundary

Search indexes only explicitly safe nested metadata. It may include canonical Task/Run/Result/Artifact relationships, policy/version, stage, outcome, requested verifier kind, machine-verifier Agent/model/provider identifiers, exact subject type/id/revision, and policy scope/stage metadata.

It deliberately excludes:

- exact subject digests from global text;
- free-form findings and review comments;
- evidence payloads and evidence-artifact contents;
- arbitrary verifier references, including human reviewer identities;
- policy metadata, provenance payloads, and creator references.

Exact subject digest/revision provenance remains available only through the authorized canonical Verification resource read. Authorization is enforced again before Search results, counts, exact-ID existence, snippets, or facets become caller-visible.

## Policy discovery

Versioned Verification Policies use the stable northbound reference `<policy_id>@<version>`, matching the versioned-resource pattern already used by Evaluation suites. Policy Search visibility is controlled by the canonical `verification-policy:list` action, while direct reads use `verification-policy:read`.
