# Reusable Templates

Issue #78 introduces a platform-owned Template system for reusable configuration intent.
Templates package configuration for Agents, Agent Teams, workflow/plans, Projects,
Workspace structures, Automations, model-routing policies, capability assignments and
composite solutions without turning runtime state into reusable configuration.

## Boundary

A Template is not a live Agent session, Run, worker job, provider session or exported
runtime snapshot. Canonical Templates remain independent from one orchestrator, model
provider, plugin implementation or deployment topology.

The current core is implemented under `ai_multi_agent_platform.templates` and defines:

- stable Template IDs and immutable revision history;
- draft and published revisions;
- owner/project/organization scope hooks;
- canonical payload or external configuration references;
- Template-to-Template dependencies;
- capability, plugin, connector, model-policy, permission, workspace and placeholder
  requirements;
- author/source/trust provenance and clone/fork lineage;
- compatibility metadata;
- dependency/compatibility preview;
- pluggable resource handlers that instantiate ordinary canonical resources;
- source-revision provenance for each instantiation.

## Versioning

Every stored revision is immutable. Editing creates a new draft revision. Publishing also
creates a new immutable revision instead of mutating the draft in place. The stable
Template definition tracks both the latest revision and latest published revision.

This means an instance created from `template_x@2` remains linked to that exact source
even if `template_x@4` is later published. There is no automatic mutation of prior
instances.

## Dependency resolution

Composite Templates declare explicit Template dependencies. Dependencies can pin an
exact published revision or resolve the latest published revision. Resolution is
recursive and deterministic, rejects cycles and orders dependencies before the root
Template. Optional missing dependencies are reported but do not block application.

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
- missing Template resource handlers;
- privileged capabilities;
- exact resource changes reported by handlers.

`TemplateService.apply()` refuses to instantiate when blocking compatibility checks fail.
Permission escalation is reported as a canonical `FORBIDDEN` error.

## Security rules

Payloads are validated before they enter the Template repository. Known plaintext-secret
fields such as passwords, API keys, access tokens and private keys are rejected. Known
backend-private runtime/session fields are rejected as well.

Secret references are allowed. For example, a payload may contain a `credential_ref`, but
the corresponding secret-reference placeholder must be resolved by the target deployment
before application. Secret values themselves are not part of the Template.

An external configuration reference is allowed in the canonical model, but application
is blocked until the deployment reports that the referenced configuration has been
validated. This prevents an opaque external reference from bypassing Template validation.

## Resource handlers

The Template engine does not create provider-private objects directly. Instead,
`TemplateResourceHandler` implementations map one canonical Template type to the ordinary
canonical resource service that owns that resource.

The required direction is:

```text
Template revision
    -> dependency / compatibility / permission preview
    -> TemplateResourceHandler
    -> ordinary canonical Agent / Team / Automation / Project / ... service
    -> canonical resource IDs
```

Handlers receive `TemplateInstantiationProvenance` with the exact source Template revision
and applying actor. This is the linkage used to keep instantiated resources explainable
after later Template edits.

## Next integration layer

The core intentionally keeps Control Plane and frontend concerns outside its domain
models. The next integration layer should register concrete handlers for the supported
canonical resource services, persist Templates in the production repository path, expose
list/detail/create/clone/version/preview/apply endpoints and add the Template management
surface to the frontend without redefining the contracts above.
