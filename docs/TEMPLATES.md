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
- platform, contract and Capability-version compatibility metadata;
- dependency/compatibility/security preview;
- server-owned placeholder, SecretReference and configuration-reference materialization;
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
- Composite -> dependency coordination only; it creates no private composite runtime
  object of its own.

`workflow_plan`, `model_routing_policy` and `capability_assignment` remain valid Template
types, but they are not given synthetic persistence or shadow runtime objects. Until the
platform exposes matching ordinary canonical resource services for those concepts,
preview reports their missing handler type and application is blocked. This preserves the
architectural rule that Templates configure canonical resources rather than becoming a
second resource system.

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
resources; it never mutates the resources produced by an earlier instance. When no
revision override is supplied, reapply uses the exact source revision of the previous
Template instance rather than silently switching to the newest published revision.

## Dependency resolution and authorization

Composite and other Templates can declare explicit Template dependencies. Dependencies
may pin an exact published revision or resolve the latest published revision. Resolution
is recursive and deterministic, rejects cycles and orders dependencies before the root
Template.

Missing optional dependencies are reported but do not block application. Missing required
dependencies block preview/application.

The Control Plane authorizes every resolved Template dependency before apply side effects.
Permission to apply a root Template therefore does not implicitly grant permission to use
all nested Templates.

Agent, Agent Team and Automation configuration may also target canonical Project or
Workspace scopes. Those effective targets are extracted from the materialized graph and
authorized separately before resource handlers run. Targets introduced through ordinary
placeholder bindings or external configuration references receive the same authorization
as literal target IDs. Workspace targets fail closed when no canonical
`WorkspaceProvider` is composed.

## Preview before apply

`TemplateService.preview()` validates the target deployment before any resources are
created. The report includes:

- missing required and optional capabilities;
- required and optional Capability-version incompatibilities;
- platform-version incompatibility;
- missing or incompatible named contract versions;
- missing plugins and connectors;
- missing model-policy references;
- permissions the applying actor cannot grant;
- missing workspace prerequisites;
- unresolved ordinary and SecretReference placeholders;
- external configuration references without concrete server-owned payloads;
- missing canonical Template resource handlers;
- privileged capabilities;
- exact resource changes reported by handlers.

Draft revisions may be previewed explicitly so users can inspect compatibility before
publishing. Canonical application uses published revisions only and runs through
`TemplateApplicationService.apply()` (or the corresponding Control Plane command). That
integrated service performs graph trust validation, materialization and compensation in
addition to the low-level compatibility preview. Permission escalation is reported as a
canonical `FORBIDDEN` error.

`TemplateService` remains the definition/versioning/preview primitive used by the
integrated application service and by focused contract tests. Deployment-facing apply
paths must use `TemplateApplicationService` so contextual dependency remapping,
materialization, trust and rollback semantics cannot be bypassed.

## Server-resolved environment

Compatibility, authorization and binding state is server-owned. Control Plane clients
cannot claim that capabilities, versions, plugins, connectors, model policies,
permissions, Workspaces, placeholders, SecretReferences or external configuration
payloads are available. Supplying those fields in a Template preview/apply command is
rejected.

`PlatformTemplateEnvironmentResolver` provides the integration seam for trusted deployment
state. It is conservative by default: an inventory that is not connected is empty rather
than assumed to be available.

For materialization, a name-only claim is not sufficient. The standard resolver marks an
ordinary placeholder, SecretReference placeholder or external configuration reference as
resolved only when it has the corresponding concrete server-owned value:

- ordinary placeholder -> JSON-compatible binding value;
- secret placeholder -> canonical `SecretReference`;
- configuration reference -> concrete validated configuration object.

When the composed Control Plane exposes a canonical `workspace_provider`, Template
registration automatically uses it for Workspace prerequisite resolution. Workspaces are
filtered to the request actor's owner identity, so a foreign Workspace is not treated as a
satisfied prerequisite merely because its ID exists in the deployment.

Single-node composition resolves available Capability IDs and versions from the canonical
Capability registry, platform version from the running package and globally grantable
permission actions from the persisted authorization policy. Scoped or approval-gated
actions are not over-reported as globally grantable.

Plugin, connector, model-policy and named contract inventories are connected only when a
canonical provider with matching semantics exists in the deployment. They are not
fabricated from adjacent concepts. For example, model configuration IDs are deliberately
not treated as model-routing-policy references.

## Materialization

Stored Templates retain portable intent. Placeholder values and external configuration
payloads are not copied into the immutable Template revision when a Template is applied.
Instead, `TemplateApplicationService` resolves the complete dependency graph and creates
ephemeral materialized revision copies before the first resource handler runs.

Ordinary placeholders have two forms:

- an exact scalar placeholder such as `${retry_limit}` may bind any JSON-compatible value;
- an embedded placeholder such as `Agent ${suffix}` requires a string binding.

Secret placeholders must occupy the complete scalar. They materialize to canonical
`SecretReference` metadata and may never be interpolated into a larger plaintext string.
Secret values themselves are never resolved by the Template engine.

`TemplateConfiguration.reference` is similarly dereferenced only from a concrete
server-owned configuration payload. A name marked as validated without an actual payload
does not make Preview applicable.

The whole graph is materialized before any handler is allowed to create a resource. A
missing or invalid binding in a later dependency therefore cannot leave earlier resources
partially created.

## Security rules

Payloads are validated before they enter the Template repository. Known plaintext-secret
fields such as passwords, API keys, access tokens and private keys are rejected. Known
backend-private runtime/session fields are rejected as well.

The same validation is repeated after materialization. This is important because an
external configuration payload or an object-valued ordinary placeholder can introduce
fields that were not present in the stored Template. Such fields are rejected before any
resource handler runs.

A Template cannot grant permissions the applying actor is not allowed to grant. Privileged
capabilities remain visible in preview instead of being silently accepted.

## Trust and portable imports

Trust is revision-local and behavioral, not descriptive metadata only.

Portable imports are downgraded to `untrusted` in the target deployment while preserving
source provenance. Any untrusted revision anywhere in an apply dependency graph blocks
normal application before canonical resources are created.

Activation is explicit and append-only. The Control Plane uses `template.publish` with
`activate_untrusted=true` to validate the current published untrusted revision and append a
new `trusted` revision linked to the exact source revision. The original imported revision
remains immutable and untrusted.

The Template repository enforces the transition boundary as a final guard: an untrusted
lineage may append another untrusted revision, but it cannot become `local` or `trusted`
through an ordinary revise/publish path. The only permitted trust promotion is a valid
explicit activation revision.

## Resource handlers and canonical ID semantics

The Template engine does not create provider-private objects directly. Instead,
context-aware Template resource handlers map one Template type to the ordinary canonical
resource service that owns that resource.

The required direction is:

```text
Template revision
    -> dependency / compatibility / permission preview
    -> trust + server-owned binding materialization
    -> target-scope authorization
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

The same rule is used by Workspace structures that depend on a Project Template.

## Failure safety

Integrated apply tracks resources created by contextual handlers and invokes available
compensators in reverse order if a later handler fails. If compensation is incomplete,
the resulting error includes both the original apply failure and explicit uncompensated or
compensation-failure evidence.

Agent Team export follows the same fail-closed principle while constructing reusable
Template graphs. Child and parent Template IDs are registered for cleanup before
persistence. If child publication or parent creation fails, every Template created by that
export attempt is deleted in reverse order. Cleanup failure preserves both the original
export error and the failed cleanup evidence.

## Durable repository

`JsonTemplateRepository` persists Template definitions, complete immutable revision
histories and Template instantiation records using a versioned JSON document. Single-node
composition stores this repository in the normal deployment data path and restores it on
restart.

Persistence contains canonical Template configuration and provenance only. It does not
serialize provider sessions, worker jobs, active runs, credentials or plaintext secrets.

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

`template.publish` normally publishes a draft. For an already-published untrusted
revision, the explicit payload `activate_untrusted=true` selects the trust-activation
operation instead.

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
- explicit required and optional version-compatibility diagnostics;
- explicit privileged-capability warnings and blocking reasons;
- publish, edit/version, clone and fork;
- explicit activation for published untrusted revisions;
- apply only after a successful preview of the current published non-untrusted revision;
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
- exact-source reapply semantics;
- composite dependency ordering and cycle/missing-dependency handling;
- full dependency-graph and materialized Project/Workspace authorization;
- missing capability/plugin/connector/model-policy/workspace requirements;
- platform, contract and Capability-version compatibility;
- permission escalation rejection and live grantable-permission resolution;
- privileged capability preview;
- plaintext-secret and backend-runtime-state exclusion before storage and after materialization;
- ordinary placeholder, SecretReference and external-reference materialization;
- graph-wide pre-side-effect materialization;
- canonical Agent, Agent Team, Automation, Project and Workspace instantiation;
- portable Team member/leader/delegation ID remapping;
- provenance linkage to exact Template revision;
- trust downgrade on import, explicit activation and promotion-bypass prevention;
- Agent Team export compensation;
- durable repository restart restoration;
- server-resolved environment behavior and owner-scoped Workspace inventory;
- provider/orchestrator replacement compatibility;
- frontend command contracts, compatibility diagnostics, activation path, tests and production build.
