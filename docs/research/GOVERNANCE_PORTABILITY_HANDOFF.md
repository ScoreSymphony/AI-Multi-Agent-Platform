# Research governance and portability handoff (#589)

This active branch prepares the remaining governed Research-to-Decision and portability boundaries
for later inclusion in the unified integration branch.

## Research -> Decision

`ResearchDecisionBridge` projects a decision-ready `ResearchItem` into the existing #598 Decision
Record authority. It first calls `ResearchService.build_action_context(...)`, so the Decision path
fails closed when Research contains unsupported/disputed Claims, lacks current supporting Evidence,
or lacks current exact #86 Verification when verification is required.

The resulting Decision Record contains exact immutable references for:

- ResearchItem ID + revision + digest;
- Claim ID + revision + digest;
- Evidence ID + SourceObservation identity + Evidence digest;
- accepted Verification IDs.

The bridge does not approve a decision, activate a downstream resource, mutate a Plan, or bypass
#15. #598 remains the Decision authority.

## Research bundle portability

`export_research_bundle(...)` exports one complete ResearchItem evidence graph including Sources,
SourceObservations, Claims, Evidence and Research-side Verification bindings. Exact historical IDs,
source revisions/digests, provider provenance and supersession references are retained.

`import_research_bundle(...)` cross-validates the complete graph before writing it. Existing exact
records are idempotent; an ID already bound to different content fails closed.

### Verification trust boundary

A serialized `ResearchVerificationBinding` is **not** sufficient to restore trusted review state.
When a bundle contains such bindings the importer requires a `VerificationBindingValidator`.
`canonical_verification_binding_validator(...)` accepts a binding only when the target deployment's
existing #86 `VerificationService` has a completed PASS for the exact same subject type, ID,
revision and digest.

This prevents portability from becoming a second Verification authority.

## #79 package adapter

`portability/research_codecs.py` adds `ResearchBundlePortableCodec` with resource type
`research_item_bundle` and `HISTORICAL_PRESERVE` identity semantics. The codec:

- carries the owner-domain Research bundle through the canonical #79 package envelope;
- marks project/workspace/task/plan/run provenance as optional resource dependencies;
- marks referenced #86 Verification records as required dependencies;
- never performs downstream activation.

There is intentionally no generic rollback mutation handler in this active branch. The generic #79
executor cannot safely compensate arbitrary partial Research writes with the current
`ResearchRepository` contract because that contract has no delete/transactional replacement seam.
Pretending rollback is possible would weaken the ownership model. The verified package should be
handed to the owner-domain `import_research_bundle(...)`, which can enforce the deployment-specific
#86 validator.

## Aggregate-branch integration steps

When all active branches are combined:

1. register `ResearchBundlePortableCodec` in the shared #79 serializer registry;
2. decide whether the #79 executor should gain a transaction-capable Research mutation seam; do not
   add a no-op rollback handler;
3. compose the same deployment-owned #86 validator used by canonical Verification persistence;
4. combine the Search, Evaluation/Research-Team and Single-Node #589 branches;
5. run the prepared Research acceptance/E2E cases only on the unified integration branch;
6. keep #589 open until the aggregate branch proves the complete source -> evidence -> verification
   -> Plan/Decision provenance path.

Per-branch CI, CodeQL and smoke execution are intentionally deferred for this preparation branch.
