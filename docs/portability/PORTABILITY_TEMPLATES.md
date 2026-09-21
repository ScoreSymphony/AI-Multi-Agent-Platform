# Template portability

Portability provides cross-deployment transport for the canonical Template domain. It does not redefine Template creation, publishing, preview/application or instantiation semantics.

## Portable resource

The portable resource type is `template`. A resource contains the canonical `TemplateDefinition` plus the complete immutable `TemplateRevision` history from revision 1 through the current revision. Draft/published state, latest-published pointer, ownership scope, configuration, requirements, compatibility metadata and provenance are preserved.

The portable Template schema version is independent from the outer package format version so Template payload migration can evolve without redefining the package container.

## Dependencies

Template portability declares dependencies for canonical references that affect cross-deployment reconstruction or applicability:

- Template dependencies are canonical `template` resource dependencies and preserve pinned revision constraints;
- Project and Organization scope references are canonical resource dependencies;
- capability, plugin and connector requirements retain their identifiers and optional/version semantics where available;
- model-routing references are represented as `model_routing_policy` resource dependencies rather than being rewritten as model IDs;
- provenance `source_template` references are optional Template dependencies.

Placeholders and secret-reference placeholders remain part of the Template requirements but secret values are never exported. A reusable Template can therefore preserve the fact that a local secret must later be bound without making the secret value portable.

## ID and reference remapping

When import uses regenerated IDs, the Template codec remaps only canonical references whose ownership is known:

- the Template ID itself;
- cross-Template dependencies;
- provenance `source_template` references;
- Project/Organization scope references;
- durable model-routing-policy references once those resources participate in the accepted import mapping.

Opaque configuration payload strings are not guessed to be IDs and are not recursively rewritten. This prevents portability from corrupting backend- or domain-specific configuration it does not own.

## Import and rollback

Import restores the complete revision history through the canonical `TemplateRepository`. The final persisted `TemplateDefinition` is the exact imported definition after accepted ID/reference remapping.

The repository exposes a guarded compensation seam for portability transactions. A freshly imported Template can be removed during rollback only while no `TemplateInstantiation` refers to it. Once an instantiation exists, compensation refuses deletion rather than erasing durable application history.

`JsonTemplateRepository` persists the same guarded compensation behavior atomically.

## Security boundary

Every exported and imported revision is checked by the canonical Template configuration validator. Plaintext secret fields and runtime-private configuration therefore remain rejected before a Template becomes portable or is written to the destination repository.

Template portability never installs plugins, creates connector credentials, resolves secret values or silently invents missing policy/routing domains. Missing required dependencies remain visible in the Portability dry-run/preview and block mutation.

## Current dependency ownership

Template portability uses the registered canonical portability owners for dependencies whose domains are available. The current composition includes Project, Model Routing Profile, Authorization Policy Profile and EvaluationSuite portability.

Dependencies without a registered canonical owner/codec remain visible in preview and fail closed. Portability never invents shadow persistence merely to make a Template import succeed.
