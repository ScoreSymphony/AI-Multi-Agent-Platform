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
The target identity, owner and assignment scope cannot move under an existing assignment ID;
changing those semantics requires a new canonical assignment.

## Target validation and scope

`CapabilityAssignmentTargetResolver` is the canonical target lookup seam.
`CallableCapabilityAssignmentTargetResolver` composes the existing Agent, Agent Team and Project
repositories without storing their implementation details. Target project/organization scope is
checked against the assignment resource scope before mutation. Missing project/organization scope
is inherited from the resolved canonical target; explicit conflicting scope is rejected.

Create performs a first authorization check before target resolution, preventing an unauthorized
caller from using validation errors as a canonical-target existence oracle. If target resolution
adds canonical scope, the request is authorized again under that exact resolved scope before
persistence. Reads use the immutable stored scope and do not require a historical target to remain
present forever.

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

The standard single-node profile composes this repository at
`db/capability-assignments.json`. It is lazy/optional until the first assignment is written, and
is listed in the authoritative single-node durable-store inventory so ordinary backup/restore
includes it whenever present.

## Template integration

The #78 `CapabilityAssignmentTemplateHandler` instantiates `capability_assignment` Templates by
calling `CapabilityAssignmentService`; it does not write assignment state into the Template
repository. A Template may target an existing canonical Agent, Agent Team or Project directly, or
resolve the target from a resource created earlier in the same Template dependency graph.

Template provenance is converted into minimal canonical assignment provenance containing the
exact source Template revision and attributable user/service actor. Privileged/approval flags are
preserved, and the assignment service re-enters the shared #15 `AuthorizationGate` before durable
creation.

The single-node profile registers this handler against the same long-lived assignment service and
shares the same `AuthorizationGate` used by Control Plane approval enforcement.

## Portability boundary

#79 can later add codecs/remapping around the same canonical repository/service without redefining
assignment identity or granting authority merely because an asset was imported. Provider handles,
runtime sessions and secrets remain outside the portable assignment state for the same reason they
are excluded from local persistence.
