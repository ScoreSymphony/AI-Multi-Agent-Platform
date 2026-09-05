# Reusable Templates

Issue #78 introduces a platform-owned Template system for reusable configuration intent.
Templates package configuration for Agents, Agent Teams, workflow/plans, Projects,
Workspace structures, Automations, model-routing policies, capability assignments and
composite solutions without turning runtime state into reusable configuration.

## Boundary

A Template is not a live Agent session, Run, worker job, provider session or exported
runtime snapshot. Canonical Templates remain independent from one orchestrator, model
provider, plugin implementation or deployment topology.

The Template system lives under `ai_multi_agent_platform.templates` and provides:

- stable Template IDs and immutable revision history;
- editable draft revisions and immutable published revisions;
- owner/project/organization scope;
- canonical inline configuration payloads or external configuration references;
- Template-to-Template dependencies;
- capability, plugin, connector, model-policy, permission, workspace and placeholder
  requirements;
- author/source/trust provenance and clone/fork lineage;
- compatibility metadata;
- dependency/compatibility/security preview;
- durable Template and instantiation persistence;
- Control Plane resources and commands;
- concrete handlers that instantiate ordinary canonical resources;
- source-revision provenance for every Template instantiation;
- a browser Template management surface at `/templates`.

## Supported Template types

The canonical type vocabulary is intentionally broader than the resource services that
exist in every deployment:

- `agent`
- `agent_team`
- `workflow_plan`
- `project`
- `workspace_structure`
- `automation`
- `model_routing_policy`
- `capability_assignment`
- `composite`

The current platform has concrete canonical handlers for:

- Agent -> `AgentService`;
- Agent Team -> `AgentService` with portable member/delegation remapping;
- Automation -> the canonical Automation service;
- Project -> `ScopeStore`;
- Workspace structure -> the canonical `WorkspaceProvider`, optionally depending on a
  Project Template so generated Workspaces bind to the newly generated Project ID;
- Capability assignment -> the #366 `CapabilityAssignmentService`, with either a direct
  canonical Agent/Agent Team/Project target or a target produced by an earlier Template
  dependency;
- Composite -> dependency coordination only; it creates no private composite runtime
  object of its own.

`workflow_plan` and `model_routing_policy` remain valid Template types, but they are not
given synthetic persistence or shadow runtime objects. Until the platform exposes matching
ordinary canonical resource services for those concepts, preview reports their missing
handler type and application is blocked. This preserves the architectural rule that
Templates configure canonical resources rather than becoming a second resource system.

Capability assignments follow that rule explicitly: the Template handler calls the #366
owning service and returns the resulting `cap_assignment_*` resource ID. Template storage
never becomes an assignment store.

## Versioning

Every stored revision is immutable. Editing appends a new draft revision. This is allowed
whether the current revision is a draft or already published. Publishing also appends a
new immutable published revision instead of mutating the draft in place.

The stable Template definition tracks both the current revision and the latest published
revision. Therefore an instance created from `template_x@2` remains linked to exactly
`template_x@2` even if later revisions are created or published.

There is no automatic mutation of prior instances.

Clone and fork create independent Template identities while retaining explicit lineage to
the source Template revision. Reapply creates a new Template instance and new canonical
resources; it never mutates the resources produced by an earlier instance.

## Dependency resolution

Composite and other Templates can declare explicit Template dependencies. Dependencies
may pin an exact published revision or resolve the latest published revision. Resolution
is recursive and deterministic, rejects cycles and orders dependencies before the root
Template.

Missing optional dependencies are reported but do not block application. Missing required
dependencies block preview/application.

## Preview before apply

`TemplateService.preview()` validates the target deployment before any resources are
created. The report includes:

- missing required and optional capabilities;
- missing plugins and connectors;
- missing model-policy references;
- permissions the applying actor cannot grant;
- missing workspace prerequisites;
- unresolved ordinary and secret-reference placeholders;
- external configuration references that have not been validated;
- missing canonical Template resource handlers;
- privileged capabilities;
- exact resource changes reported by handlers.

Draft revisions may be previewed explicitly so users can inspect compatibility before
publishing. Application uses published revisions only. `TemplateService.apply()` refuses
to instantiate when blocking compatibility checks fail. Permission escalation is reported
as a canonical `FORBIDDEN` error.

## Server-resolved environment

Compatibility and authorization environment state is server-owned. Control Plane clients
cannot claim that capabilities, plugins, connectors, model policies, permissions,
Workspaces, placeholders, secret references or external configuration references are
available. Supplying those fields in a Template preview/apply command is rejected.

`PlatformTemplateEnvironmentResolver` provides the integration seam for trusted deployment
state. It is conservative by default: an inventory that is not connected is empty rather
than assumed to be available.

When the composed Control Plane exposes a canonical `workspace_provider`, Template
registration automatically uses it for Workspace prerequisite resolution. Workspaces are
filtered to the request actor's owner identity, so a foreign Workspace is not treated as a
satisfied prerequisite merely because its ID exists in the deployment.

Other inventories can be injected through matching canonical providers. Model
configuration IDs are deliberately not treated as model-routing-policy references because
the concepts have different semantics.

## Security rules

Payloads are validated before they enter the Template repository. Known plaintext-secret
fields such as passwords, API keys, access tokens and private keys are rejected. Known
backend-private runtime/session fields are rejected as well.

Secret references are allowed. For example, a payload may contain a `credential_ref`, but
the corresponding secret-reference placeholder must be resolved by the target deployment
before application. Secret values themselves are never part of the Template.

An external configuration reference is allowed in the canonical model, but application
is blocked until the deployment reports that the referenced configuration has been
validated. This prevents an opaque external reference from bypassing Template validation.

A Template cannot grant permissions the applying actor is not allowed to grant. Privileged
capabilities remain visible in preview instead of being silently accepted.

For `capability_assignment`, required/allowed privileged or approval-gated rules remain
explicit in the Template payload, and the #366 service re-enters the ordinary #15
`AuthorizationGate` before canonical assignment persistence.

## Resource handlers and canonical ID semantics

The Template engine does not create provider-private objects directly. Instead,
context-aware Template resource handlers map one Template type to the ordinary canonical
resource service that owns that resource.

The required direction is:

```text
Template revision
    -> dependency / compatibility / permission preview
    -> TemplateResourceHandler
    -> ordinary canonical Agent / Team / Automation / Project / Workspace / ... service
    -> canonical resource IDs
```

Handlers receive `TemplateInstantiationProvenance` with the exact source Template revision
and applying actor. A shared `TemplateInstantiationContext` carries canonical resources
created earlier in the dependency graph so later handlers can remap portable references.
For example, an Agent Team Template points to Agent Template dependencies rather than
persisting source Agent IDs; the Team handler resolves those dependencies to the new Agent
IDs created in the same Template instance.

The same rule is used by Workspace structures that depend on a Project Template and by
capability-assignment Templates whose target is produced by an Agent, Agent Team or Project
Template dependency.

## Durable repository

`JsonTemplateRepository` persists Template definitions, complete immutable revision
histories and Template instantiation records using a versioned JSON document. Single-node
composition stores this repository in the normal deployment data path and restores it on
restart.

Persistence contains canonical Template configuration and provenance only. It does not
serialize provider sessions, worker jobs, active runs, credentials or plaintext secrets.

Capability-assignment state is not copied into this repository. It lives in the #366
`JsonCapabilityAssignmentRepository` and participates independently in normal deployment
backup/restore.

## Control Plane API

The composed Control Plane exposes the canonical collections:

- `templates`
- `template-instances`

Core commands are:

- `template.create`
- `template.revise`
- `template.publish`
- `template.clone`
- `template.fork`
- `template.preview`
- `template.apply`
- `template.reapply`

Creation from existing canonical resources is supported for the resource sets currently
owned by concrete handlers:

- `template.create-from-agent`
- `template.create-from-agent-team`
- `template.create-from-automation`
- `template.create-from-project`
- `template.create-from-workspaces`

All commands run through the normal Control Plane authorization and idempotency path.
Template list/detail and Template-instance list/detail are ordinary Control Plane
resources rather than provider-specific endpoints.

## Frontend

The browser UI exposes Template management at `/templates` and Template detail at
`/templates/:templateId`.

The surface includes:

- Template library/list;
- creation from Agent, Agent Team, Automation, Project and Workspace structures;
- advanced canonical Template JSON creation for composite/extensible types;
- revision, scope, compatibility and provenance details;
- dependency and requirement display;
- server-resolved compatibility/permission preview;
- explicit privileged-capability warnings and blocking reasons;
- publish, edit/version, clone and fork;
- apply only after a successful preview of the current published revision;
- revision history;
- Template instances and links to generated canonical resources.

The browser never sends its own environment claims. Mutating Template commands use the
same authenticated browser-session/CSRF Control Plane transport as the rest of the web UI.

## Examples

Validated example Template-content documents live under `examples/templates/`. They are
examples of portable configuration contracts, not mandatory platform roles or built-in
business-domain presets.

## Verification

Issue #78 tests cover the required lifecycle and safety cases, including:

- create/revise/publish and immutable histories;
- clone/fork lineage;
- composite dependency ordering and cycle/missing-dependency handling;
- missing capability/plugin/connector/model-policy/workspace requirements;
- permission escalation rejection;
- privileged capability preview;
- plaintext-secret and backend-runtime-state exclusion;
- secret-reference and external-reference validation;
- canonical Agent, Agent Team, Automation, Project, Workspace and capability-assignment
  instantiation;
- portable Team member/leader/delegation ID remapping;
- provenance linkage to exact Template revision;
- reapply/new-version behavior without silent mutation;
- durable repository restart restoration;
- server-resolved environment behavior and owner-scoped Workspace inventory;
