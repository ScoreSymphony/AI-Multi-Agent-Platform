# Capability assignment policies

Issue #366 introduces a durable canonical resource for reusable capability/tool assignment
intent. It deliberately sits **next to** the Capability Registry instead of replacing it.

## Ownership boundary

The platform owns a stable `cap_assignment_*` identity and immutable revision history. Each
revision targets one canonical Agent, Agent Team or Project and contains disjoint `required`,
`allowed` and `denied` canonical capability references.

The #12 Capability Registry remains authoritative for capability identity, versions, features,
availability and invocation metadata. Assignment persistence never stores provider IDs, provider
tool handles, MCP session IDs, Worker runtime state, credentials or plaintext secrets.

The #15 authorization boundary remains authoritative for create, revise, read and list
operations. Privileged assignment changes are submitted with elevated/high risk to the normal
`AuthorizationGate`; the assignment service does not invent a second permission engine.

## Version and conflict semantics

A rule may select an exact capability version or use the existing provider-neutral
`CapabilityCompatibilityRequest`. The two are mutually exclusive. Canonical capability
references are validated against the Registry inventory before persistence, including feature
and comparable numeric-version constraints.

Within one assignment revision, the same capability ID cannot appear in more than one of
`required`, `allowed` or `denied`. This avoids implicit precedence rules. Revisions advance by
exactly one and old revisions remain available by exact `(assignment_id, revision)` reference.

## Target validation and scope

`CapabilityAssignmentTargetResolver` is the canonical target lookup seam.
`CallableCapabilityAssignmentTargetResolver` composes the existing Agent, Agent Team and Project
repositories without storing their implementation details. Target project/organization scope is
checked against the assignment resource scope before mutation.

## Privileged capabilities

Registry metadata remains the source for safety, destructive side effects, credential
requirements and provider-declared approval requirements. A required/allowed assignment for a
privileged capability must mark that fact explicitly; a Registry capability that declares an
approval requirement cannot be silently assigned without `approval_required=True`.

These flags describe assignment intent. Effective invocation authorization and actual approval
validity still belong to #12/#15 at invocation time.

## Persistence

`JsonCapabilityAssignmentRepository` stores the canonical policy identity and complete immutable
revision history in a schema-versioned JSON document using atomic replacement. The serialized
shape contains only canonical scope, target, rule, provenance and compatibility fields.

## Template and portability integration

This resource is the owning domain required by #78's `capability_assignment` Template type.
Template application can now instantiate through `CapabilityAssignmentService` rather than
creating Template-private shadow state. #79 can later add codecs/remapping around the same
canonical repository/service without redefining assignment identity or granting authority merely
because an asset was imported.
