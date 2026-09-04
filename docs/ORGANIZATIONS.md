# Organization, Team and Membership Domain (#87)

This document records the canonical organization, team and membership foundation for issue #87.

## Boundaries

The organization domain owns durable organizational relationships and resource scope metadata:

- organizations;
- collaboration teams;
- memberships;
- human invitations;
- personal scope semantics;
- ownership and explicit sharing records;
- external identity-provider group mappings.

It does **not** authenticate actors, store credentials, or make final permission decisions. Authentication remains owned by #36 and effective permission decisions remain owned by #15. Membership roles and policy references are inputs to authorization, not a replacement authorization engine.

## Personal scope

An actor can operate without an Organization. `membership_authorization_scope(..., organization_id=None)` yields a personal scope with no synthetic organization or team IDs. Personal `OwnerRef(type="user", ...)` resources therefore remain first-class and can later be transferred or shared explicitly.

## Membership lifecycle

Membership rows are historical records. Removal and voluntary leave change status and record a revocation timestamp; they do not delete the canonical relationship or erase actor identity. Suspended/revoked/left memberships are excluded from membership-derived active authorization scope.

The initial lifecycle is:

`active -> suspended -> revoked`

and:

`active -> left`

Invitation lifecycle is:

`pending -> accepted | expired | revoked`

Invitation records contain only a secure `token_ref`; token material and credentials remain outside this domain. Northbound invitation projections deliberately omit `token_ref`, so secret references are not exposed through list/get responses.

## Authorization bridge

`membership_authorization_scope` projects active team IDs plus role/policy references for a selected organization. `actor_identity_for_scope` can construct the existing #15 `ActorIdentity` for that organization.

`MembershipAuthorizationProvider` is a deny-only guard that can wrap any canonical #15 `AuthorizationProvider`. For Organization/Team-scoped requests it rechecks the current Organization, Team and Membership state before delegating to #15. It also understands the existing Control Plane `owner_type`/`owner_id` scope, including deriving an Organization from a Team owner scope.

Active membership role/policy references are projected into the richer #15 request `trust_context` under `organization_membership`. Caller-supplied values under that key are overwritten with repository-backed values. The wrapped #15 provider remains responsible for interpreting those values and returning the actual allow/deny/approval decision. Changing a membership role/policy assignment therefore changes canonical authorization input without creating a second authorization engine.

This prevents a stale `ActorIdentity` from retaining future access after suspension/removal while preserving the original identity referenced by historical Tasks, Runs and Events.

## Ownership and sharing

`ResourceOwnership` uses the platform-owned `OwnerRef`, so resources can remain personal or be owned by an organization, team, or service. `ResourceShare` records explicit grants and revocation.

Cross-organization team/organization sharing is denied by default and requires an explicit `allow_cross_organization=True` call. This is still not sufficient authorization by itself; callers must pass normal #15 authorization before mutating ownership/share state.

For resources that already own a canonical `OwnerRef` themselves, that resource model remains the ownership source of truth. The #87 ownership layer is intended to mirror that identity and add sharing/scope metadata rather than create an independently mutable second owner. Explicit integration adapters for the existing resource APIs remain required before those mirrors are considered complete.

## Persistence

`OrganizationRepository` is the canonical persistence seam. Two implementations currently exist:

- `InMemoryOrganizationRepository` for deterministic tests/reference flows;
- `SqliteOrganizationRepository` for restart-safe local/self-hosted persistence.

The SQLite implementation persists Organizations, Teams, Memberships, Invitations, Ownership records, Shares and external group mappings behind the same port. Membership updates replace lifecycle state without changing the canonical membership ID or erasing actor/creation history. A unique `(resource_type, resource_id)` index enforces one ownership record per canonical resource.

## Control Plane and runtime composition

When an `OrganizationService` is configured, the current Control Plane composes the following canonical collections:

- `organizations`;
- `teams`;
- `memberships`;
- `invitations`;
- `resource-ownerships`;
- `resource-shares`;
- `external-group-mappings`.

Mutation commands cover Organization creation/archive, Team create/update, Membership add/assignment/suspend/remove, Invitation create/accept/revoke, Ownership set/transfer, Share create/revoke and external group mapping creation.

Organization-sensitive mutations receive an additional owner-scoped #15 check before the generic command authorization. Scope-aware resource services filter individual records before caller-visible pagination/counts, so an otherwise authorized collection read cannot enumerate another Organization's resources. Hidden exact IDs return not-found semantics.

`invitation.accept` is intentionally not preconditioned on an active membership: the invited actor does not have that membership until acceptance succeeds. The normal authenticated principal and generic #15 command authorization still apply.

The Organization composition is layered above the current `automation_runtime_composition` rather than an older Control Plane base. This preserves the current Automation runtime and any later-domain registrations already present on `main`; Organization management is additive rather than a replacement composition.

## Audit and membership history

When an `organization_audit_events` `EventProvider` is configured, the Control Plane also registers `organization-audit-events`.

Successful Organization, Team, Membership and Invitation lifecycle commands are projected into the existing canonical `Event` model. Audit events:

- use deterministic IDs derived from the logical idempotency key, command and resource reference;
- correlate by canonical Organization ID;
- retain the authenticated acting principal in `provenance.actor_ref`;
- record only an allowlisted lifecycle projection such as affected resource/member, status and role/policy references;
- never copy invitation token references or credential material;
- are read through the existing `EventProvider` rather than a parallel audit datastore.

Audit list/get visibility is Organization-scope-aware. Owners, Organization administrators and active members can see history for Organizations visible to them; an outside principal receives an empty scoped collection and not-found semantics for exact hidden audit IDs.

## Historical provenance

Removing a Membership never mutates Task/Run/Event records. Regression coverage creates a Task attributed to a member, removes that membership, then confirms that the canonical Task owner and original Event `provenance.actor_ref` remain unchanged while the Membership itself becomes revoked.

This is intentionally distinct from the audit projection: historical execution provenance explains who originally acted, while Organization audit history explains later membership and collaboration changes.

## External IdP groups

`ExternalGroupMapping` stores provider-owned group IDs as reversible mappings to canonical Organization/Team IDs. External group IDs never become canonical platform IDs.

## Remaining integration work

The remaining #87 work is primarily product/integration surface rather than the core relationship model:

1. frontend Organization, Team, member, invitation, role/policy and context-switch surfaces;
2. explicit ownership mirrors/adapters for existing Project/Workspace/Agent/File/Automation and other reusable resource APIs, with the pre-existing resource `OwnerRef` remaining authoritative;
3. final end-to-end Definition-of-Done audit against every acceptance criterion and required test after the integration surfaces are complete.
