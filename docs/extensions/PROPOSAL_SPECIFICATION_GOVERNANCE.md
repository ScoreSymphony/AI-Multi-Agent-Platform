# Proposal and Specification governance

Issue #501 adds an **optional** governance layer before canonical Task execution. It does
not change the execution hierarchy: `Goal -> Task -> Plan -> Step -> Run` remains the
platform-owned execution model, and callers may continue creating a Task directly.

## Ownership and invariants

- A **Proposal** is a versioned intake artifact for ideas, problems, opportunities and
  machine-generated signals. It may be revised, request clarification, be dismissed,
  superseded, specified or converted.
- A clarification request is a dedicated lifecycle operation that creates a new Proposal
  revision in `needs_spec`; it is not a terminal state and does not create a Task.
- Proposal supersession preserves owner, Project and Workspace scope across the lineage.
  The replacement is authorized as a new Proposal as well as through the supersede command,
  so supersession cannot be used to create a cross-scope Proposal.
- `dismissed`, `superseded` and `converted_to_task` are terminal Proposal states. Dedicated
  lifecycle operations enforce the same terminal-state rule; a Proposal cannot move from
  one terminal state to another.
- A **Specification** is an immutable, explicitly revisioned review contract. A material
  change creates a new revision and content digest.
- A Specification never executes. Conversion creates exactly one canonical `Task`, and
  all runtime planning/execution starts from that Task.
- Canonical Approval ownership remains in the #15 security layer. Governance stores no
  second approval lifecycle.
- An approval for Task conversion is bound to the exact `Specification` ID, revision and
  SHA-256 content digest through the canonical `ProposedAction.digest` contract. A later
  material revision therefore invalidates the previous approval for conversion.
- Proposal and Specification state is durable and recoverable independently from Search.
  Search indexes only derived, authorization-filtered projections and is never canonical.

## Specification content

A Specification revision captures the reviewable execution contract, including problem,
goal, scope/out-of-scope, acceptance criteria, dependencies, constraints, risk,
capability/model/agent requirements, data/security constraints, validation strategy,
required tests and verification, human gates, decomposition hints, assumptions and open
questions.

The stable content digest excludes timestamps and revision counters. Approval binding
contains both the explicit revision number and the content digest: identical content can
therefore be recognized as identical content while the reviewed revision is still exact.

## Approval and conversion

High-risk (`high` or `critical`) Specifications and Specifications with one or more
`required_human_gates` cannot convert without a valid canonical Approval. Governance
creates/looks up that Approval through `AuthorizationGate`/`ApprovalService`; approval
decisions still pass through the normal #15 approver authorization path.

Conversion is restart-safe:

1. durable governance storage reserves one Task ID for the exact Specification;
2. canonical `PlatformKernel.create_task` is called with a deterministic idempotency key;
3. exact governance provenance is attached to Task metadata with a second deterministic
   idempotency key;
4. the conversion record is marked complete;
5. a Proposal intake is marked `converted_to_task` while its history remains intact.

A crash between these steps can be retried after restart. The reservation preserves the
Task ID and the kernel command log preserves Task/event idempotency, so a retry converges
on the same canonical Task rather than creating a duplicate.

The durable reservation also establishes a Proposal lifecycle invariant. For a
Proposal-backed Specification, reservation and Proposal-terminal-state validation occur
inside the same governance-store write transaction. Once a Task conversion is reserved,
the source Proposal cannot subsequently become `dismissed` or `superseded`; this prevents
a concurrent terminal transition from producing a durable Task followed by an externally
reported conversion failure. The SQLite baseline also rejects a second Specification from
reserving another Task conversion for the same Proposal.

A Proposal that is already `dismissed`, `superseded` or `converted_to_task` is rejected
before a new conversion reservation or Task side effect. This validation is repeated by
the durable SQLite reservation boundary rather than relying only on an in-process read.

Completed conversion replay is different from new work. If the exact Specification
revision/digest already has a `completed` conversion, the canonical existing Task is
returned before a fresh Approval check. Approval expiry after successful conversion cannot
retroactively make the completed side effect fail or create a second Task. If a crash
occurred after conversion completion but before the Proposal was marked
`converted_to_task`, replay repairs that Proposal state when it remains legally mutable.
Legacy terminal-state drift is surfaced as a governance audit event instead of hiding the
already durable Task.

The Task metadata namespace `governance` records the upstream Proposal ID (when present),
Specification ID/revision/content digest, Approval ID when applicable, acceptance
criteria, constraints, risk, required capabilities/tests/verification and human gates.
This metadata is provenance/context only; the Task owns execution state from that point
forward.

`db/governance.sqlite3` is an optional platform-owned single-node durable store. The
existing backup inventory includes it whenever present and permits it to be materialized
after restore, so enabling optional governance does not invalidate an older backup that
predates the store.

### Approval and replay observability

Canonical #15 Approval state and its authorization audit remain authoritative for the
actual Approval decision lifecycle; #501 does not duplicate that authority. Governance
adds safe linkage/projection events around the Specification contract:

- `specification.approval-requested` when governance requests canonical Approval;
- `specification.approval-resolved` when an exact approved binding is consumed for a new
  conversion attempt;
- `specification.approval-binding-invalidated` when a material revision changes the exact
  Specification digest that prior Approval could have authorized;
- `specification.stale-approval-rejected` when an obsolete binding is supplied;
- `specification.converted-to-task` when conversion completes;
- `specification.conversion-replayed` when a completed conversion is deterministically
  returned again without re-authorizing the already completed side effect.

These events contain IDs, revisions, digests and outcome metadata only; unrestricted
Specification content is not copied into governance audit records.

## Control Plane and Search

The domain registers these current resources:

- `proposals`
- `specifications`
- `proposal-revisions`
- `specification-revisions`
- `governance-events`

Revision and audit collections are not Search-indexed. Proposal/Specification Search
projections intentionally contain only discovery metadata and short title/summary data;
full review content is returned only by the authorized canonical resource APIs. Search
hits are re-authorized against the current canonical governance resource before return.

Mutation commands are:

- `proposal.create`
- `proposal.revise`
- `proposal.request-clarification`
- `proposal.dismiss`
- `proposal.supersede`
- `specification.create`
- `specification.revise`
- `specification.request-approval`
- `specification.convert-to-task`

As with other mutating Control Plane commands, `Idempotency-Key` is required by the
canonical command boundary. Proposal writes use expected revisions for optimistic
concurrency; clarification therefore cannot silently overwrite a newer Proposal revision.

## Web and CLI clients

The web governance surface uses the same registered Control Plane collections and command
boundary as every other client. It provides the Proposal inbox, Proposal and Specification
detail views, clarification and supersession actions, Specification revision comparison,
Approval links, Task conversion and resulting Task links. `Request clarification` invokes
the canonical `proposal.request-clarification` command and moves the Proposal to
`needs_spec` rather than maintaining browser-private lifecycle state.

The CLI discovers registered extension collections and commands from canonical OpenAPI.
`extension execute` sends mutations to `/api/v1/commands/{command}` with the caller's
idempotency key; it does not contain a separate Proposal/Specification state machine.
Approval decisions continue through the existing canonical Approval surface.

## Planning integration (#439)

`GovernanceService.planning_input(...)` exposes an immutable exact-revision projection for
planning. When the Specification requires approval, the same exact action binding must be
approved before this projection is returned. Planning may consume the goal, acceptance
criteria, constraints, decomposition hints, required tests and verification requirements;
it does not own or rewrite Proposal/Specification state.

## Optional signal intake and adapters

`create_proposal_from_signal(...)` is the platform-owned optional seam for Automation,
monitoring or review signals. It creates an ordinary canonical Proposal with source,
evidence, confidence/value/risk and fingerprint metadata. No external issue-tracker or
workflow adapter is required for the governance domain to function; such adapters may map
external records into these contracts later without becoming canonical state.