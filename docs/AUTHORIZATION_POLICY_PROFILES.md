# Authorization Policy Profiles

Issue: #310

Authorization policy profiles are the platform-owned, provider-neutral representation of
reusable permission configuration. They sit above the issue-#15 authorization boundary and
must not be confused with a concrete policy engine's configuration.

## 1. Ownership boundary

The canonical resource is an `AuthorizationPolicyProfileDefinition` plus immutable
`AuthorizationPolicyProfileRevision` history.

`AuthorizationProvider` remains authoritative for effective authorization decisions.
`AuthorizationGate` remains authoritative for server-side enforcement and approval binding.
A profile therefore does not grant authority merely because it exists, is imported, is
referenced by another resource, or has been assigned as configuration.

`LocalPrincipalPolicy` is deliberately not canonical. It is private configuration for the
replaceable `LocalAuthorizationProvider` reference implementation. A canonical profile may
be compiled into that local shape only when the local provider can represent every
canonical constraint. Unsupported conditions fail closed.

## 2. Canonical identity and revisions

A profile has a stable canonical ID with prefix `authorization_policy_profile_` and a
monotonically increasing immutable revision history. `AuthorizationPolicyProfileRef`
identifies one exact historical revision.

The stable definition owns:

- canonical profile ID;
- owner reference;
- current revision pointer;
- optional Project, Organization and Team scope;
- lifecycle enabled/disabled state;
- creation and update timestamps.

Each immutable revision owns:

- exact revision number;
- canonical owner/scope snapshot;
- provider-neutral policy content;
- creation timestamp.

Historical revisions are never rewritten when a later revision is created or when the
active authorization provider is replaced.

An explicit Project, Organization or Team scope on the profile/revision is an outer
security bound for permission-bearing content. Inner policy constraints may be empty or
repeat that same bound, but they cannot point outside it. When an inner constraint is
empty, compatible provider compilation inherits the outer bound rather than interpreting
the omission as globally unrestricted authority.

## 3. Provider-neutral policy content

The current canonical vocabulary supports:

- allowed canonical `AuthorizationAction` values;
- actions that require approval;
- canonical `ResourceType` constraints;
- Project, Organization, Team, Workspace and exact resource-ID constraints;
- required security labels;
- allowed node IDs;
- allowed side-effect classifications;
- descriptive metadata and explicit provenance;
- schema versioning.

Provider-native policy documents, provider object IDs, compiled rules, credentials,
plaintext secrets and backend runtime state are not canonical profile content.

The platform does not make UI roles such as `admin`, `editor` or `viewer` canonical policy
entities. A UI may present such labels, but durable authority is expressed with the
canonical authorization vocabulary above.

## 4. Provenance and imported configuration

`AuthorizationPolicyProvenance` records who created the configuration, its source and an
optional source reference. Portable decoding marks imported revisions explicitly as
`imported=True` and `trusted=False` before mutation can occur.

Trust metadata is descriptive input to governance; it is never itself a grant. In
particular, an imported profile that contains `administer` or another privileged action
cannot authorize its own activation or assignment.

## 5. Lifecycle service and approvals

`AuthorizationPolicyProfileService` is the canonical management and import boundary. It
owns:

- create/read/list;
- immutable revision creation;
- explicit enable/disable lifecycle changes;
- exact-revision assignment;
- safe import of complete dormant/untrusted histories;
- narrowly-scoped import compensation for package rollback.

The service is constructed with the existing `AuthorizationGate`, so user-visible profile
management and import application use the normal issue-#15 decision and approval path
rather than a policy-profile-specific decision engine.

Mutations are treated as privileged operations:

- create/revise: high-risk configuration changes;
- import: critical creation of permission-bearing configuration;
- enable: critical activation step;
- disable: administrative change;
- assignment: critical administrative change.

If the configured provider returns `require_approval`, the ordinary exact-action approval
workflow applies. The profile service does not implement a second approval model. Critical
imports additionally bind that exact action to a SHA-256 fingerprint computed by the
canonical service over the destination definition and complete revision history. An
approval for one policy body therefore cannot authorize changed content under the same
policy ID and revision.

## 6. Assignments are configuration, not authority

`AuthorizationPolicyAssignment` binds one principal to one exact profile revision as
durable configuration. The assignment record itself does not modify an
`AuthorizationProvider` and does not authorize requests.

Applying or translating assigned configuration into a concrete provider remains a
separate privileged operation and must pass the normal authorization/approval boundary.
This separation is required so replacing the provider cannot change canonical profile
identity or silently turn imported configuration into authority.

Disabled profiles cannot be assigned. A portable import is always disabled, so the target
actor must separately pass the authorization boundary to enable it and then separately
pass the boundary again to create an assignment.

## 7. Local reference-provider compilation

`compile_local_principal_policy()` translates one exact canonical revision into
`LocalPrincipalPolicy` only when the local provider can represent the complete revision.
It rejects unsupported canonical conditions and exact resource-ID constraints instead of
silently dropping them.

Project, Organization and Team constraints inherit any corresponding outer profile scope
when the inner constraint is empty. Because the local reference provider treats an empty
scope set as unrestricted, this inheritance is required to preserve the canonical outer
security boundary.

The resulting `LocalPrincipalPolicy` has no canonical identity of its own. Recompiling the
same profile for a different compatible provider does not mutate or re-key the canonical
profile.

## 8. Persistence

`InMemoryAuthorizationPolicyProfileRepository` is the deterministic reference repository.
`JsonAuthorizationPolicyProfileRepository` adds dependency-free durable persistence with
full immutable revision history and exact-revision assignments.

Both repositories support atomic complete-history import and guarded removal for import
compensation. The JSON repository rolls back the corresponding in-memory mutation whenever
a durable write fails, including create, revision append, lifecycle changes, assignment,
complete-history import and guarded deletion. The live process therefore cannot report a
policy mutation that would disappear after restart.

The JSON store contains canonical configuration only. Secret values, provider-native
objects and compiled provider policy state are excluded. Repository restoration validates
contiguous revision history and profile/revision identity consistency before accepting the
stored state.

## 9. Portability boundary (#79)

Cross-deployment transport uses the existing issue-#79 package, validation, preview,
remapping and rollback infrastructure.

`AuthorizationPolicyProfilePortableCodec` serializes:

- the stable profile definition;
- the complete immutable revision history;
- canonical dependency references for typed Project, Organization, Team, Workspace and
  Node scopes.

It never serializes assignments, effective provider grants, provider-native policy objects
or credentials.

The normal #79 `IdPolicy` supports preserving or regenerating the profile identity.
Canonical typed scope references are remapped through `ImportContext`. Opaque exact
resource IDs are preserved rather than guessed into a resource type.

### Preview security inspection

The generic #79 preview supports resource-specific `ImportSecurityFinding` values. Policy
profiles report:

- `untrusted_configuration` to make the dormant/untrusted import state explicit;
- `permission_escalation` with the potential direct actions, approval-gated actions and
  resource types carried by the profile;
- blocking `invalid_security_payload` when a package attempts to transport assignments or
  the policy payload cannot be safely inspected.

Blocking security findings make the import preview not ready before mutation.

### Safe import

`AuthorizationPolicyProfileImportMutationHandler` delegates materialization to
`AuthorizationPolicyProfileService.import_profile()`. The handler:

1. verifies that the decoded target matches the accepted preview mapping;
2. requires the decoded profile to remain disabled;
3. requires every imported revision to be explicitly imported and untrusted;
4. replaces source ownership with an explicit destination `OwnerRef`;
5. invokes the canonical service rather than writing the repository directly;
6. creates no assignments;
7. returns a rollback token only after complete import succeeds.

`compensate_import()` can remove only a disabled, unassigned, imported/untrusted profile.
It is not a general deletion API and fails closed once the profile has been enabled or
assigned.

The production portability composition enables policy-profile transport only when the
canonical repository, canonical policy service, explicit import authorization context and
explicit destination owner are supplied together. Partial configuration is rejected.

This preserves the invariant:

```text
portable configuration
    -> validation + security preview
    -> canonical dormant/untrusted profile
    -> separately authorized enable
    -> separately authorized assignment/application
```

and explicitly rejects:

```text
portable package -> effective authority
```

## 10. Related domains

- #15 owns authorization semantics, enforcement and approval binding.
- #34 owns secret references and secret handling.
- #79 owns package export/import, validation, preview, remapping and rollback.
- #78 Templates may reference exact policy-profile revisions but do not own profile
  identity/history.
- #87 Organization/Team membership configuration may reference policy profiles without
  redefining authorization semantics.
