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
- create-from-existing exporters for supported canonical resources;
- source-revision provenance for every Template instantiation;
- a browser Template management surface at `/templates`.

## Supported Template types

The canonical type vocabulary is intentionally broader than the resources that any one
runtime must expose:

- `agent`
- `agent_team`
- `workflow_plan`
- `project`
- `workspace_structure`
- `automation`
- `model_routing_policy`
- `capability_assignment`
- `composite`

The current standard platform has concrete canonical handlers for all of these types:

- Agent -> `AgentService`;
- Agent Team -> `AgentService` with portable member/delegation remapping;
- Workflow/Plan -> the #364 `AuthorizedWorkflowService`;
- Automation -> the canonical Automation service;
- Project -> `ScopeStore`;
- Workspace structure -> the canonical `WorkspaceProvider`;
- Model-routing policy -> the #309 `ModelRoutingProfileService`;
- Capability assignment -> the #366 `CapabilityAssignmentService`;
- Composite -> dependency coordination only, with no private composite runtime object.

The architectural rule remains unchanged: Templates configure resources owned by canonical
domains. They do not introduce Template-private persistence for Workflows, routing profiles,
capability assignments, Agents, Teams or other runtime resources.

### Workflow/Plan

`workflow_plan` Templates instantiate through #364. Workflow stages are materialized into
canonical `WorkflowStage` values, Template compatibility is preserved as canonical
`WorkflowCompatibility`, and exact Template revision plus Template instance provenance are
stored in the resulting Workflow revision. Agent and Agent-Team stage references can be
expressed as Template dependencies and remapped to resources created earlier in the same
application graph.

Workflow create-from-existing reads the canonical Workflow through
`AuthorizedWorkflowService`, reconstructs a reusable Workflow Template, and emits portable
Agent/Agent-Team Template dependencies where required. It never exports active execution
state or provider/orchestrator sessions.

### Capability assignment

`capability_assignment` Templates instantiate through #366 and return ordinary
`cap_assignment_*` resources. Direct canonical Agent/Agent-Team/Project targets and targets
produced by earlier Template dependencies are supported.

Create-from-existing reads the canonical immutable assignment revision, preserves the
provider-neutral required/allowed/denied rule set, and stores only canonical capability and
target references. Provider tool handles, runtime sessions and credentials are excluded.

### Model-routing policy

`model_routing_policy` Templates instantiate through #309 `ModelRoutingProfileService`.
They create ordinary durable routing profiles using canonical model-configuration IDs,
provider-neutral `RoutingRequirements`, ordered model preferences and deterministic
fallback policy.

The standard Template composition reuses the same #309 service already registered in the
Control Plane rather than constructing a second routing-policy owner. Template provenance
records the exact source revision and Template instance. Guarded compensation can remove
only an untouched revision-1 profile created by that exact failed Template application.

Create-from-existing uses that same authorized #309 service to read an exact routing-profile
revision and reconstruct only its canonical policy intent. The resulting Template records
source profile ID/revision provenance but does not copy provider runtime state, sessions or
backend-private identifiers.

## Versioning

Every stored revision is immutable. Editing appends a new draft revision. Publishing also
appends an immutable published revision instead of mutating an earlier revision in place.
The stable Template definition tracks both current revision and latest published revision.

An instance created from `template_x@2` remains linked to exactly `template_x@2` even when
later revisions are published. Template updates never silently mutate previous instances or
the canonical resources created from them.

Clone and fork create independent Template identities while retaining explicit lineage.
Reapply creates a new Template instance and new canonical resources. With no explicit
revision override it reuses the exact source revision of the previous instance rather than
silently selecting the newest published revision.

## Dependency resolution and authorization

Dependencies may pin an exact revision or resolve the latest published revision. Resolution
is recursive and deterministic, rejects cycles, and orders dependencies before the root.
Missing optional dependencies are reported without blocking; missing required dependencies
block apply.

The Control Plane authorizes every resolved Template dependency before side effects.
Permission to apply a root Template therefore never implies permission to use every nested
Template.

Materialized Agent, Agent Team and Automation payloads that refer to Project or Workspace
scopes are authorized before handlers run. Literal IDs, ordinary placeholder bindings and
external configuration references receive the same target-scope checks. Workspace targets
fail closed when no canonical `WorkspaceProvider` is composed.

Workflow target Project/Organization scope comes from the stored Template revision and
creation re-enters #364 authorization. Capability-assignment creation re-enters #366
authorization. Model-routing profile creation re-enters #309 authorization. Template apply
authorization is never reused as authority for the different canonical resource mutation.

## Preview before apply

`TemplateService.preview()` validates the target deployment before resources are created.
Reports include:

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
- missing canonical Template handlers;
- privileged capabilities;
- exact resource changes reported by handlers.

Drafts may be previewed explicitly. Deployment-facing apply must use
`TemplateApplicationService`, which adds graph trust validation, materialization,
context-aware dependency remapping and reverse-order compensation around the low-level
preview contract.

## Server-resolved environment

Compatibility, authorization and binding state is server-owned. Clients cannot claim that
capabilities, versions, plugins, connectors, model policies, permissions, Workspaces,
placeholders, SecretReferences or external configuration payloads are available.
Caller-supplied environment claims are rejected.

`PlatformTemplateEnvironmentResolver` is conservative by default: an inventory that is not
connected is empty rather than assumed present.

The standard public Single-Node composition resolves:

- Capability IDs and versions from the canonical Capability Registry;
- live Connector Definition IDs from the composed canonical Connector Registry;
- enabled model-routing policy references from the canonical #309 repository;
- platform version from the running package;
- globally grantable actions from persisted authorization state;
- Workspace prerequisites from the composed canonical Workspace provider.

`TemplateRequirements.connector_ids` therefore uses canonical `ConnectorDefinition.id`
values, preserving the exact connector type/version identity. A durable Connector Definition
without a currently registered implementation is not treated as available; registration and
unregistration are reflected dynamically in subsequent Template preview/apply resolution.

Plugin and named contract inventories are connected only when a canonical provider with
matching semantics exists. Adjacent state is never fabricated into a compatibility claim.

## Materialization

Stored Templates retain portable intent. Ordinary placeholder values, SecretReference
bindings and external configuration payloads are materialized only into ephemeral revision
copies used for preview/apply.

An exact scalar placeholder such as `${retry_limit}` may bind any JSON-compatible value.
Embedded interpolation such as `Agent ${suffix}` requires a string. Secret placeholders
must occupy the complete scalar and materialize only to canonical `SecretReference`
metadata; plaintext secret values are never resolved by the Template engine.

`TemplateConfiguration.reference` is dereferenced only from a concrete server-owned
configuration payload. Merely marking a reference name as validated is insufficient.

The complete dependency graph is materialized before the first resource handler can create
anything. Invalid later bindings therefore cannot leave earlier graph resources partially
created.

## Security rules

Payloads are validated before persistence and again after materialization. Known plaintext
secret fields such as passwords, API keys, access tokens and private keys are rejected, as
are backend-private runtime/session fields.

A Template cannot grant permissions the applying actor is not allowed to grant. Privileged
capabilities remain visible during preview. Imported/untrusted Templates require explicit
validation and activation before ordinary apply.

Capability-assignment privileged/approval-gated rules stay explicit and re-enter #366's
ordinary authorization boundary. Workflow scope re-enters #364. Model-routing policy
creation re-enters #309. None of these domains accept a Template apply decision as a
substitute for their own authorization contract.

## Trust and portable imports

Trust is revision-local and behavioral. Portable Template imports are downgraded to
`untrusted` while preserving source provenance. Any untrusted revision anywhere in the
resolved graph blocks normal apply before canonical resources are created.

Activation is explicit and append-only. `template.publish` with
`activate_untrusted=true` validates the current published untrusted revision and appends a
new trusted revision linked to the exact source revision. Ordinary revise/publish paths
cannot promote an untrusted lineage to local/trusted state.

## Canonical resource and ID semantics

The required direction is:

```text
Template revision
    -> dependency / compatibility / permission preview
    -> trust + server-owned materialization
    -> target-scope authorization
    -> contextual Template handler
    -> canonical resource service
    -> canonical resource IDs
```

`TemplateInstantiationContext` carries resources created earlier in the graph, allowing a
Team to remap Agent dependencies, a Workspace structure to bind a newly created Project, a
Capability Assignment to target an Agent/Team/Project created earlier, and a Workflow stage
to bind an Agent/Team created in the same Template instance.

## Failure safety

Integrated apply tracks resources and invokes available compensators in reverse order when
a later handler fails. If cleanup is incomplete, the resulting error preserves both the
original apply failure and compensation evidence.

Capability Assignment, Workflow and Model Routing Policy handlers use guarded canonical
compensation. They may remove only untouched resources whose owner and exact Template
source provenance match the failed application; Workflow and routing-profile compensation
also bind cleanup to the exact Template instance. Successful compensation is persisted by
the owning repository.

Agent Team export is failure-safe as well. Child and parent Template IDs are registered for
cleanup before persistence; failures remove every Template created by that export attempt
in reverse order, while cleanup errors remain visible alongside the original failure.

## Durable repositories

`JsonTemplateRepository` stores Template definitions, immutable histories and Template
instances. It does not store provider sessions, worker jobs, active Runs, credentials,
plaintext secrets or copies of canonical resources created from Templates.

Canonical domain state remains in its owner store, including:

- capability assignments -> #366 `JsonCapabilityAssignmentRepository`;
- Workflows -> #364 `JsonWorkflowRepository` (`db/workflows.json` in Single-Node);
- model-routing profiles -> #309 `JsonModelRoutingProfileRepository`.

These stores participate independently in the deployment's normal persistence and
backup/restore semantics.

## Control Plane API

The composed Control Plane exposes:

- `templates`
- `template-instances`

Lifecycle commands include:

- `template.create`
- `template.revise`
- `template.publish`
- `template.clone`
- `template.fork`
- `template.preview`
- `template.apply`
- `template.reapply`

Create-from-existing commands in the standard composition include:

- `template.create-from-agent`
- `template.create-from-agent-team`
- `template.create-from-workflow`
- `template.create-from-capability-assignment`
- `template.create-from-model-routing-profile`
- `template.create-from-automation`
- `template.create-from-project`
- `template.create-from-workspaces`

Workflow, Capability Assignment and Model Routing Profile export commands are registered
only when their canonical handlers/services are actually composed. This keeps optional
deployments fail-closed instead of advertising unsupported operations.

All commands use the ordinary Control Plane authorization and idempotency path. Routing
profile export additionally performs its source read through the authorized #309 service
before any Template draft is persisted.

## Frontend

The browser UI exposes Template management at `/templates` and detail at
`/templates/:templateId`.

The surface includes:

- Template library/list;
- creation from Agent, Agent Team, Workflow, Capability Assignment, Model Routing Profile,
  Automation, Project and Workspace structures;
- advanced canonical Template JSON creation for composite/extensible types;
- revision, scope, compatibility and provenance details;
- dependency and requirement display;
- server-resolved compatibility/permission preview;
- required and optional version diagnostics;
- privileged-capability warnings and blocking reasons;
- publish, edit/version, clone and fork;
- explicit activation for published untrusted revisions;
- apply only after a successful preview of the current published trusted revision;
- revision history;
- Template instances and links to generated canonical resources.

The Template route is manifest-gated. It is considered available only when the complete
required resource/command contract, including Workflow, Capability Assignment and Model
Routing Profile create-from-existing commands, is present. The browser never invents
missing backend capability.

Mutating Template commands use the shared authenticated BrowserSession/CSRF/idempotency
transport.

## Examples

Validated Template-content examples live under `examples/templates/`. They are portable
configuration examples, not mandatory platform roles or public marketplace entries.

## Verification

Issue #78 regression coverage includes:

- create/revise/publish and immutable history;
- clone/fork lineage;
- exact-source reapply and instance/source authorization;
- composite dependency ordering, cycles and missing dependencies;
- complete dependency-graph and materialized Project/Workspace authorization;
- platform, contract and Capability-version compatibility;
- permission escalation rejection and live grantable-permission resolution;
- plaintext-secret/runtime-private-state exclusion before persistence and after materialization;
- ordinary placeholder, SecretReference and external-reference materialization;
- graph-wide pre-side-effect materialization;
- canonical Agent, Agent Team, Workflow, Automation, Project, Workspace,
  Capability Assignment and Model Routing Policy instantiation;
- Workflow Agent/Team dependency remapping and exact Template-instance provenance;
- guarded Workflow, Capability Assignment and routing-profile compensation;
- Capability Assignment, Workflow and Model Routing Profile create-from-existing exporters;
- source-read authorization before canonical owner-domain export mutation;
- live Connector Definition inventory in the public Single-Node Template environment;
- Agent Team export compensation;
- untrusted import downgrade, explicit activation and promotion-bypass prevention;
- durable restart behavior;
- standard Single-Node Template composition for #309/#364/#366 and #44 Connector inventory;
- frontend typed create-from-existing commands, manifest gating, preview diagnostics,
  activation, tests and production build;
- provider/orchestrator replacement compatibility.
