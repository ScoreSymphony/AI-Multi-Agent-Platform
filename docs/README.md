# Documentation

This directory contains the product, architecture, runtime, integration, operations and historical documentation for AI Multi-Agent Platform.

## Start here

The following cross-cutting documents stay at the `docs/` root because they define or index platform-wide behavior:

- [`PRODUCT_VISION.md`](PRODUCT_VISION.md) — product identity, goals and scope.
- [`ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md) — binding architecture principles and invariants.
- [`DOMAIN_MODEL.md`](DOMAIN_MODEL.md) — canonical platform domain model.
- [`CONTRACTS.md`](CONTRACTS.md) — replaceable provider and adapter contracts.
- [`KERNEL.md`](KERNEL.md) — Task/Run lifecycle ownership and recovery.
- [`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md) — dependency-driven implementation plan and current convergence work.
- [`DEVELOPMENT.md`](DEVELOPMENT.md) — development setup and validation entry point.
- [`SECURITY_THREAT_MODEL.md`](SECURITY_THREAT_MODEL.md) — cross-cutting threat model.
- [`UPSTREAMS.md`](UPSTREAMS.md) — architecture-significant upstream inventory and provenance overview.

Additional cross-cutting entry documents such as frontend, planning, release, conformance and upstream policy material remain at this level when they are referenced across several documentation areas.

## Topic directories

| Directory | Purpose |
| --- | --- |
| [`runtime/`](runtime/) | Agent runtime, execution, Control Plane, models, capabilities, persistence, messaging, workflows and Workspaces. |
| [`product/`](product/) | Product-facing domains and surfaces such as conversations, notifications, organizations, templates, browser/terminal, onboarding and accounting. |
| [`cli/`](cli/) | CLI overview and command-area documentation. |
| [`search/`](search/) | Global Search and domain-specific Search integrations. |
| [`integrations/`](integrations/) | Concrete adapters and external-system integrations, including Hermes, Forge, LiteLLM, Connectors and repository integration. |
| [`extensions/`](extensions/) | Plugin/extension compatibility, Registry/Marketplace and optional platform-expansion domains. |
| [`security/`](security/) | Authentication, authorization, secrets and secure development/deployment guidance. |
| [`operations/`](operations/) | Deployment, HA, backup/restore, observability, upgrades, host pressure and release operations. |
| [`portability/`](portability/) | Import/export and portability contracts for platform resources. |
| [`quality/`](quality/) | Evaluation, Verification and quality evidence. |
| [`quality/benchmarks/`](quality/benchmarks/) | Performance, pressure, degradation, distributed and coordination benchmark documentation. |
| [`history/issues/`](history/issues/) | Historical issue-completion, progress and audit records retained as implementation evidence rather than active architecture entry points. |
| [`adr/`](adr/) | Architecture Decision Records. |
| [`schemas/`](schemas/) | Documentation-owned machine-readable schemas. |
| [`upstream/`](upstream/) | Detailed upstream adoption/reuse evidence. |
| [`examples/`](examples/) | Documentation examples. |
| [`reconciliations/`](reconciliations/) | Reconciliation records and supporting evidence. |

## Placement rules

Keep `docs/` itself small. New documentation should follow these rules:

1. Put only genuinely cross-cutting canonical documents or high-level indexes at the `docs/` root.
2. Put subsystem and feature documentation in the matching topic directory.
3. Put CLI and Search extensions in their dedicated directories instead of adding more prefixed files to the root.
4. Put performance/load/stress evidence under `quality/benchmarks/`.
5. Put issue-specific completion, progress, finalization and audit records under `history/issues/`; do not add new `ISSUE_*` files to the `docs/` root.
6. Keep ADRs, schemas, upstream evidence, examples and reconciliation records in their existing dedicated directories.
7. When moving a document, update repository-relative links and references in the same change.

GitHub issue state and current issue wording remain the point-in-time source of truth for implementation status. Historical documents under `history/` are retained for traceability and should not be treated as newer than current canonical documentation or merged code.
