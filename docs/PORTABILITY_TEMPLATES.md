# Template portability

Issue #79 provides cross-deployment portability for the canonical Template domain owned by #78. It does not redefine Template creation, publishing, preview/application or instantiation semantics.

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

Every exported and imported revision is checked by the canonical #78 Template configuration validator. Plaintext secret fields and runtime-private configuration therefore remain rejected before a Template becomes portable or is written to the destination repository.

Template portability never installs plugins, creates connector credentials, resolves secret values or silently invents missing policy/routing domains. Missing required dependencies remain visible in the #79 dry-run/preview and block mutation.

## Current dependency ownership

Template portability is complete for the canonical #78 contracts. References whose canonical domains are not yet available remain deliberately fail-closed:

- Project portability depends on #308;
- durable model-routing-policy portability depends on #309;
- reusable authorization-policy portability depends on #310;
- Evaluation suite/result portability depends on #19.

Those issues own their canonical persistence contracts. #79 integrates them only after those contracts exist rather than introducing shadow stores inside portability.
