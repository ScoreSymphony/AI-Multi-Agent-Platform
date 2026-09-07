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

The Task metadata namespace `governance` records the upstream Proposal ID (when present),
Specification ID/revision/content digest, Approval ID when applicable, acceptance
criteria, constraints, risk, required capabilities/tests/verification and human gates.
This metadata is provenance/context only; the Task owns execution state from that point
forward.

`db/governance.sqlite3` is an optional platform-owned single-node durable store. The
existing backup inventory includes it whenever present and permits it to be materialized
after restore, so enabling optional governance does not invalidate an older backup that
predates the store.

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
detail views, Specification revision comparison, Approval links, Task conversion and
resulting Task links. `Request clarification` invokes the canonical
`proposal.request-clarification` command and moves the Proposal to `needs_spec` rather
than maintaining browser-private lifecycle state.

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
