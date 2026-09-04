# Organization, Team and Membership Domain (#87)

This document records the canonical organization, team and membership foundation for issue #87.

## Boundaries

The organization domain owns durable organizational relationships and resource collaboration metadata:

- organizations;
- collaboration teams;
- memberships;
- human invitations;
- personal scope semantics;
- ownership mirrors and explicit sharing records;
- external identity-provider group mappings.

It does **not** authenticate actors, store credentials, or make final permission decisions. Authentication remains owned by #36 and effective permission decisions remain owned by #15. Membership roles and policy references are inputs to authorization, not a replacement authorization engine.

## Personal scope

An actor can operate without an Organization. `membership_authorization_scope(..., organization_id=None)` yields a personal scope with no synthetic organization or team IDs. Personal resources therefore remain first-class; Organization creation is never mandatory for single-user/self-hosted use.

## Organization ownership lifecycle

An Organization has one canonical `owner_actor_id` plus zero or more administrator actor references. Administrators are not owners and cannot take ownership merely because they are administrators.

`organization.owner.transfer` provides the explicit transfer required before the current owner can leave the Organization. The command:

- is Organization-scoped and still passes through #15;
- additionally requires the authenticated acting principal to be the current `owner_actor_id`;
- requires the target actor to already hold an active Membership in the Organization;
- changes only the canonical Organization owner reference and timestamp;
- is projected into canonical Organization audit history.

The previous owner is not automatically promoted to administrator. If the previous owner has a Membership, that Membership can be left normally after transfer.

## Membership lifecycle

Membership rows are historical records. Removal and voluntary leave change status and record a revocation timestamp; they do not delete the canonical relationship or erase actor identity. Suspended/revoked/left memberships are excluded from membership-derived active authorization scope.

Lifecycle paths include:

`active -> suspended -> revoked`

and:

`active -> left`

The Organization owner cannot leave before transferring Organization ownership.

Invitation lifecycle is:

`pending -> accepted | expired | revoked`

Invitation records contain only a secure `token_ref`; token material and credentials remain outside this domain. Northbound invitation projections deliberately omit `token_ref`, so secret references are not exposed through list/get responses.

## Authorization bridge

`membership_authorization_scope` projects active team IDs plus role/policy references for a selected Organization. `actor_identity_for_scope` can construct the existing #15 `ActorIdentity` for that Organization.

`MembershipAuthorizationProvider` is a deny-only guard that can wrap any canonical #15 `AuthorizationProvider`. For Organization/Team-scoped requests it rechecks current Organization, Team and Membership state before delegating to #15. It also understands existing Control Plane `owner_type`/`owner_id` scope, including deriving an Organization from a Team owner scope.

Active Membership role/policy references are projected into the #15 request `trust_context` under `organization_membership`. Caller-supplied values under that key are overwritten with repository-backed values. The wrapped #15 provider remains responsible for the final allow/deny/approval decision.

This prevents stale Membership identity from retaining future access after suspension/removal while preserving the original actor referenced by historical Tasks, Runs and Events.

## Ownership and sharing

`ResourceOwnership` uses platform-owned `OwnerRef`, so collaboration metadata can represent personal, Organization, Team or service ownership. `ResourceShare` records explicit grants and revocation.

Cross-Organization sharing is denied by default. `allow_cross_organization=True` expresses caller intent but is not permission: the Control Plane additionally requires a dedicated #15 `resource-share.cross-organization` authorization decision before mutation.

For resources that already have a canonical owner, that resource remains the ownership source of truth. #87 mirrors that identity and adds collaboration/share metadata; it must not create a second independently mutable owner.

### Canonical ownership integration matrix

| Resource | Canonical ownership state | #87 behavior |
| --- | --- | --- |
| Project | canonical `OwnerRef` in project scope store | strict mirror on canonical creation; direct generic #87 owner mutation rejected |
| Workspace | canonical owner in workspace scope store | strict mirror on canonical creation; direct generic #87 owner mutation rejected |
| Agent | canonical `OwnerRef` | authoritative mirror across create/update/clone/rollback |
| Agent Team | canonical `OwnerRef` | authoritative mirror across create/update/clone/rollback |
| Automation | canonical identity owner | authoritative mirror across create/update/pause/resume/disable |
| Memory | canonical string `owner_ref` with explicit Memory scope | strict mirror on create/promote/update; Organization-scoped Memory resolves to Organization ownership |
| Knowledge Source | canonical string `owner_ref` | strict mirror on register/update; current Knowledge API creates principal-owned sources and has no canonical owner-transfer command |
| Connection | canonical `owner_type`/`owner_id` plus optional Organization/Project scope | strict mirror on create/enable/disable/health; current Connection API has no canonical owner-transfer command |
| File | canonical `FileRecord.owner_ref` | canonical owner exists, but there is currently no Control Plane File-create/owner-transfer command for #87 to hook without adding read side effects or a second owner; final integration remains open |
| Artifact | represented by artifact IDs linked from canonical File records, not a separate owner-bearing resource | ownership follows the backing File; no separate #87 Artifact owner should be invented |
| Evaluation Suite/Run | current Control Plane projection has no owner or Organization/Project owner contract | no separate #87 owner is created until the Evaluation domain defines one |
| Template | no standalone canonical owner-bearing Template resource currently exposed | not separately applicable yet |
| Plugin/configuration | current canonical plugin resources are platform installation/configuration resources without per-user/team owner contract | not separately applicable yet; secret/config authorization remains outside #87 |

A resource in the last three categories is intentionally **not** assigned a synthetic #87 owner merely to satisfy a checklist. When those domains gain canonical scope/owner semantics, they should add the same mirror integration rather than mutate `ResourceOwnership` independently.

## Persistence

`OrganizationRepository` is the canonical persistence seam. Two implementations currently exist:

- `InMemoryOrganizationRepository` for deterministic tests/reference flows;
- `SqliteOrganizationRepository` for restart-safe local/self-hosted persistence.

The SQLite implementation persists Organizations, Teams, Memberships, Invitations, Ownership records, Shares and external group mappings behind the same port. Membership lifecycle updates preserve canonical Membership IDs and actor/creation history. A unique `(resource_type, resource_id)` index enforces one ownership record per canonical resource.

## Control Plane

When an `OrganizationService` is configured, the Organization composition registers:

- `organizations`;
- `teams`;
- `memberships`;
- `invitations`;
- `resource-ownerships`;
- `resource-shares`;
- `external-group-mappings`;
- optional `organization-audit-events` when an EventProvider is configured.

Mutation commands include:

- Organization create/update/owner-transfer/archive;
- Team create/update/configure;
- Membership add/assignment/suspend/remove/leave;
- Invitation create/accept/revoke;
- ownership set/transfer for resource types whose canonical domain does not already own the owner truth;
- share create/revoke;
- external group mapping create/deactivate.

Organization-sensitive mutations receive an owner-scoped #15 check. Scope-aware resource services filter individual records before caller-visible pagination/counts, so another Organization cannot be inferred from counts or exact hidden IDs.

`invitation.accept` is intentionally usable before Membership exists: the invited actor gains the Membership only when acceptance succeeds. Authentication and the generic #15 command boundary still apply.

## Frontend

The Organizations surface includes:

- Organization list/create/archive and configuration editing;
- explicit Organization owner transfer;
- Team list/create/configuration and hierarchy selection;
- member add/assignment/suspend/remove/leave flows;
- human/service/automation Membership actor types;
- invitation create/accept/revoke;
- role and policy references;
- Organization/Team context switching with personal scope retained;
- ResourceOwnership/ResourceShare views and share/revoke controls;
- Organization collaboration audit history.

The resource panel refuses to treat canonically mirrored resource types as generically transferable when their own domain is the owner source of truth.

## Audit and historical provenance

Successful Organization, Team, Membership, Invitation and external-mapping lifecycle commands are projected into the existing canonical `Event` model. Audit events:

- use deterministic IDs derived from logical idempotency key, command and resource reference;
- correlate by canonical Organization ID;
- retain the authenticated principal in `provenance.actor_ref`;
- record an allowlisted lifecycle projection only;
- include the resulting `owner_actor_id` for Organization owner transfer;
- never copy invitation token references or credential material;
- use the existing `EventProvider` rather than a second audit database.

Audit visibility is Organization-scope-aware. Owners, Organization administrators and active members can read history for visible Organizations; outsiders receive an empty scoped collection and not-found semantics for hidden exact IDs.

Removing a Membership never mutates historical Task/Run/Event records. Regression coverage confirms original Task ownership and Event actor provenance survive Membership removal while future organizational access is revoked.

## External IdP groups

`ExternalGroupMapping` stores provider-owned group IDs as reversible mappings to canonical Organization/Team IDs. External group IDs never become canonical platform IDs. Deactivation preserves mapping history while removing the active provisioning relationship.

## Remaining #87 work

The core Organization/Team/Membership model, authorization bridge, persistence, audit, frontend collaboration surface and most reusable-resource mirrors are implemented. Remaining work before #87 can close is intentionally narrow:

1. finish the File/File-backed Artifact ownership adapter at a mutation boundary without introducing GET side effects or a second owner truth;
2. run the new Memory/Knowledge, Connection and Organization-owner-transfer tests through the full repository CI;
3. re-anchor the Organization runtime composition above the **current** `main` Control Plane stack (including later Conversation/Evaluation work) and resolve any resulting compatibility changes;
4. perform the final acceptance-criteria/required-test audit, update PR #219 and issue checkboxes, and require a full green CI before removing Draft status.
