# Portability Control Plane and CLI workflow

Issue #79 exposes canonical import/export through the same versioned northbound boundary used by the rest of the platform. Clients never construct trusted import mappings or mutation order themselves.

## Control Plane resources

When portability is composed, `/api/v1` advertises three platform-owned collections:

- `portability-packages` — integrity-checked package documents and compatibility inspection;
- `portability-import-previews` — mutation-free dependency/conflict analysis and deterministic ID mapping;
- `portability-import-reports` — completed import results.

The collections are read-only projections. Canonical destination resources are still written only by their registered resource-specific `ImportMutationHandler` implementations.

## Commands

The versioned Control Plane exposes:

- `portability.export` — select canonical source resources and build/register one portable package;
- `portability.package.validate` — parse, schema-check, checksum-check, compatibility-check and register an incoming package;
- `portability.preview` — create a server-owned dry-run for one registered package;
- `portability.import` — execute exactly one previously stored preview.

All commands use the ordinary Control Plane authorization, request/correlation and idempotency boundary. Import payloads do not accept an ID mapping or import order. The accepted mapping/order is the server-side preview created by `ImportPreviewService`.

A successfully executed preview is replay-safe: subsequent executions return the existing report instead of applying the same mutations again, even when the caller uses a different idempotency key.

## Compatibility and safety

Package validation verifies the portable schema and integrity before a package is registered. Declared minimum/maximum platform versions are inspected before import mutation. An incompatible package may still be inspected, but it cannot be executed.

Missing required dependencies or conflicts make the stored preview non-ready. Required plugin, capability, connector, model and secret-reference dependencies are never auto-installed or auto-created by portability.

Plaintext secrets and backend-private runtime state remain prohibited by the existing package/codec validation boundary. The Control Plane does not provide a second route around those checks.

## CLI

The CLI is a client of those same `/api/v1` commands and resources:

```text
platform portability export --resource agent:agent_123 --resource agent_team:team_123
platform portability export --resource project:project_123
platform portability export --resource template:template_123
platform portability export --resource evaluation_suite:portable.agent-suite@1.2
platform portability validate package.json
platform portability preview package_<checksum>
platform --yes portability import preview_<digest>
platform portability package package_<checksum>
platform portability preview-show preview_<digest>
platform portability report import_<digest>
```

`portability import` requires the global `--yes` confirmation switch because it mutates canonical destination resources. The CLI sends only the preview ID; it cannot submit or rewrite the trusted import plan.

## Current production composition

The single-node deployment composes canonical Agent, Agent Team, Project, Template and EvaluationSuite export/import against their owning-domain repositories/services. Template portability reuses the canonical #78 Template repository and immutable revision model rather than defining a second Template lifecycle inside #79.

Project portability reuses the canonical `ScopeStore`/`SqliteScopeStore` persistence completed by #308. The portable snapshot preserves the complete canonical Project identity, owner, timestamps, schema version, provenance and external references. Import writes the complete snapshot through `ScopeStore.store_project_snapshot(...)`; it does not reconstruct a reduced Project or create a portability-specific Project store.

Project rollback is deliberately fail-closed. The #308 compensation seam refuses deletion unless cross-domain dependency safety is explicitly proven and also rejects deletion when Workspace dependencies exist. A deployment that cannot provide a complete cross-domain dependency audit therefore reports incomplete compensation rather than risking deletion of a referenced Project.

EvaluationSuite portability likewise reuses an owning-domain seam rather than a portability database. Imported exact suite versions are created through `EvaluationService.create_suite(...)` and persisted by `EvaluationSuiteAssetRepository` in `evaluation.sqlite3`; rollback goes through `EvaluationService.delete_suite(...)`, is bound to the imported checksum and refuses deletion after durable run history references that suite version. The suite codec remaps canonical Agent targets through the server-owned preview and declares model/capability/fixture dependencies explicitly. Fixture dependencies currently remain fail-closed in single-node until a canonical portable EvaluationFixture owner is composed.

Portability remains an optional composition layer instead of being imported by the base `ai_multi_agent_platform.control_plane` package. Production deployments that enable portability select the portability-aware `ControlPlane` explicitly. This keeps unrelated Agent/Template/Control-Plane package initialization acyclic while preserving the same northbound API once the feature is composed.

Preview checks Model dependencies against the canonical `ModelRegistry`, Project/Workspace resource dependencies against `ScopeStore`, existing Template dependencies against the canonical Template repository, and exact EvaluationSuite identities against the Evaluation-owned suite service. EvaluationSuite Agent dependencies participate in the same package dependency ordering/remapping as other canonical resources.

Capability, plugin, connector and secret requirements remain fail-closed in this composition until their corresponding canonical production registries are explicitly wired. This is intentional: preview must not claim a dependency is available merely because its resource type exists somewhere in the codebase.

#79 is complete and closed. Project portability consumes the canonical #308 persistence seam, and #19 now provides the owning-domain EvaluationSuite mutation/persistence seam required for safe `evaluation_suite` import/export through the same workflow. Evaluation execution remains independent of portability, and fixture-bearing suites continue to fail closed until a canonical portable EvaluationFixture integration is explicitly composed. #309 and #310 remain independent follow-up domain work if durable model-routing or authorization-policy profiles are later added to the portability surface; they do not block #79's completed status.

## Execution boundary

The complete northbound flow is:

```text
canonical source resource
    -> ExportSourceRegistry
    -> ResourceSerializerRegistry
    -> PortablePackage
    -> package validation / compatibility inspection
    -> server-owned ImportPreview
    -> dependency/conflict review
    -> stored preview ID
    -> ImportExecutor
    -> resource-specific preflight
    -> dependency-ordered canonical mutation
    -> reverse compensation on failure
    -> ImportReport
```

This workflow adds operational coordination state only. It does not become a second canonical store for Agent, Team, Template, Project or any other imported resource.
