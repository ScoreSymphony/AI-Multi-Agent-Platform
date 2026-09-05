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
platform portability export --resource template:template_123
platform portability validate package.json
platform portability preview package_<checksum>
platform --yes portability import preview_<digest>
platform portability package package_<checksum>
platform portability preview-show preview_<digest>
platform portability report import_<digest>
```

`portability import` requires the global `--yes` confirmation switch because it mutates canonical destination resources. The CLI sends only the preview ID; it cannot submit or rewrite the trusted import plan.

## Current production composition

The single-node deployment composes canonical Agent, Agent Team and Template export/import against their durable repositories. Template portability reuses the canonical #78 Template repository and immutable revision model rather than defining a second Template lifecycle inside #79.

Preview checks Model dependencies against the canonical `ModelRegistry`, Project/Workspace resource dependencies against `ScopeStore`, and existing Template dependencies against the canonical Template repository.

Capability, plugin, connector and secret requirements remain fail-closed in this composition until their corresponding canonical production registries are explicitly wired. This is intentional: preview must not claim a dependency is available merely because its resource type exists somewhere in the codebase.

Project portability itself remains blocked on #308, which must first complete canonical Project persistence. Model-routing-policy portability remains blocked on #309, authorization-policy portability on #310, and Evaluation asset portability on #19. #79 does not create shadow persistence to bypass those domain owners.

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
