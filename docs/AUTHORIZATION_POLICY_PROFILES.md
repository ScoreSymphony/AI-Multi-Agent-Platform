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
optional source reference. Imported configuration can explicitly be marked `imported` and
`trusted=False`.

Trust metadata is descriptive input to governance; it is never itself a grant. In
particular, an imported profile that contains `administer` or another privileged action
cannot authorize its own assignment or activation.

## 5. Lifecycle service and approvals

`AuthorizationPolicyProfileService` is the canonical management boundary for profile
create/read/list/revise/disable and assignment operations. It is constructed with the
existing `AuthorizationGate`, so profile management uses the normal issue-#15 decision and
approval path rather than a policy-profile-specific bypass.

Mutations are treated as privileged operations:

- create/revise: high-risk configuration changes;
- disable: administrative change;
- assignment: critical administrative change.

If the configured provider returns `require_approval`, the ordinary exact-action approval
workflow applies. The profile service does not implement a second approval model.

## 6. Assignments are configuration, not authority

`AuthorizationPolicyAssignment` binds one principal to one exact profile revision as
durable configuration. The assignment record itself does not modify an
`AuthorizationProvider` and does not authorize requests.

Applying or translating assigned configuration into a concrete provider remains a
separate privileged operation and must pass the normal authorization/approval boundary.
This separation is required so replacing the provider cannot change canonical profile
identity or silently turn imported configuration into authority.

## 7. Local reference-provider compilation

`compile_local_principal_policy()` translates one exact canonical revision into
`LocalPrincipalPolicy` only when the local provider can represent the complete revision.
It rejects unsupported canonical conditions and exact resource-ID constraints instead of
silently dropping them.

The resulting `LocalPrincipalPolicy` has no canonical identity of its own. Recompiling the
same profile for a different compatible provider does not mutate or re-key the canonical
profile.

## 8. Persistence

`InMemoryAuthorizationPolicyProfileRepository` is the deterministic reference repository.
`JsonAuthorizationPolicyProfileRepository` adds dependency-free durable persistence with
full immutable revision history and exact-revision assignments.

The JSON store contains canonical configuration only. Secret values, provider-native
objects and compiled provider policy state are excluded. Repository restoration validates
contiguous revision history and profile/revision identity consistency before accepting the
stored state.

## 9. Portability boundary (#79)

Cross-deployment transport belongs to the existing issue-#79 portability layer rather than
to the policy repository format.

The portable resource must include the stable profile definition and complete immutable
revision history, support the normal preserve/regenerate ID policy and remap typed
canonical scope references through the import context.

Security requirements for policy-profile import are stricter than ordinary configuration
copying:

1. importing a profile never imports or creates effective assignments;
2. imported provenance is treated as imported/untrusted configuration on the destination;
3. preview must expose privileged/escalating policy content before mutation;
4. destination authorization must approve the import through the canonical policy
   management boundary;
5. import must use the ordinary #79 preflight/apply/rollback contract;
6. provider-private policy objects and secret material are never portable;
7. enabling or applying imported policy remains a separate authorized action.

This preserves the invariant:

```text
portable configuration -> validated canonical profile -> authorized assignment/application
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
