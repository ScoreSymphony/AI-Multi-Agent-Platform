# Changelog

All notable user-visible changes will be documented in this file.

The project uses semantic versioning once a public prototype is released. Until then, changes accumulate under `Unreleased` and GitHub issues/acceptance gates define the release readiness criteria.

## Unreleased

### Added

- Canonical platform foundations for Tasks, Plans, Steps, Runs, Events, Agents, Agent Teams, models, capabilities/tools, Projects, Workspaces, Files, Artifacts and Results.
- Versioned Control Plane with Web and CLI client paths.
- Authentication, authorization, approvals, secret-reference handling and platform security boundaries.
- Automations, Search, Memory/Knowledge, Verification/Review, Notifications, Chat, Browser and Terminal capabilities.
- Organization/Team/Membership management, practical Task-management metadata and canonical usage/resource accounting.
- Connector and Repository/Git integration, reusable workflow definitions, portable import/export and canonical Template integrations.
- Supported single-node deployment, optional Control Plane HA/failover, network-capable distributed transport and remote Workspace materialization.
- Platform-owned durable Plan/Step coordination with persisted dependency progression, fan-out/fan-in, waits, Step retries, cancellation/reconciliation, claims/fencing and backend-neutral Control Plane projections.
- Optional replaceable Registry/Marketplace foundations with offline/local discovery, compatibility/integrity/trust validation, canonical owner-domain activation, CLI commands and typed frontend client hooks.
- Canonical model-routing profiles with durable immutable revisions, authorized management, exact-revision assignment, portable provenance and fail-closed schema/history validation.
- Durable Connector persistence with restart-stable Connection, external-resource identity and SyncCheckpoint reconstruction while keeping Search derived.
- Reusable no-paid-service prototype acceptance profiles and a platform-wide conformance framework with fast/integration/release profiles and machine-readable evidence.
- Production-shaped conformance evidence for required reference-release scenarios, optional compatibility claims, Backup/Upgrade/Evaluation release checks and an authenticated cross-layer Agent/Model/Workspace/Verification vertical slice.
- Deterministic performance tooling covering concurrency sweeps, read/mixed/history/restart workloads, idle footprint, soak/endurance, bounded stress, restart-under-load and transport backpressure/outage/duplicate-delivery behavior.
- Repository governance, ownership, issue-quality automation and supply-chain review.
- Release-manifest validation, complete compatibility/provenance metadata, fail-closed release gates, advisory upstream discovery, an upstream-update PR convention and a security-hotfix runbook.
- Release-manifest schema v2 with cryptographically bound dependency/artifact provenance, typed gate evidence and complete canonical version-vector compatibility state.

### Changed

- The project has moved beyond the original architecture/prototype implementation waves; the remaining roadmap is now organized around convergence of durable workflow coordination, distributed deployment hardening, Template/Registry acceptance, measured scalability, release operationalization and final platform conformance.
- Ordinary canonical Task/Run execution can use the shared `DistributedRuntime` when advanced deployment is explicitly enabled, while the #39 single-node path remains unchanged when distributed execution is disabled.
- Reference distributed profiles are profile-aware and use the same authenticated Worker reporter/transport contracts for local and remote Workers rather than separate lifecycle models.
- Templates now integrate canonical Workflow, Capability Assignment and Model Routing Policy owner domains instead of keeping those types as unresolved/fail-closed placeholders.
- Registry activation routes through existing plugin/import/template owner domains instead of creating a second installation or mutation authority.
- Accounting integrations now derive Workspace, Node/Worker, Agent/Team, Organization/Team and budget-notification behavior from their canonical source domains while preserving #76 as the accounting authority.
- Project status documentation distinguishes merged core implementations from reopened completion-audit work and no longer treats first implementation or issue-number order as the execution plan.

### Fixed

- Model-routing profile management and portability now preserve immutable historical response semantics, monotonic chronology, exact assignment authorization across Template/import consumers, revision/schema consistency and safe compensation when other canonical resources may still reference a profile.
- Canonical Node/Worker `updated_at` semantics now represent state changes independently from heartbeat/liveness timestamps and remain monotonic across re-registration, persistence and legacy restore.
- Connector state now survives normal single-node/server restart and authorized Search reconstruction without promoting Search to canonical persistence.
- Release provenance/compatibility validation now fails closed on incomplete dependency digests, malformed source commits, unbound evidence, compatibility revision mismatches and incomplete version-vector comparisons.
- The main branch Ruff formatting regression introduced during parallel release work was corrected without behavioral changes.

### Security

- CodeQL, dependency review and scheduled dependency updates are configured.
- Authorization/approval, authentication/session, Workspace isolation, Worker transport/materialization, routing-profile assignment/compensation, Template/Registry activation and durable coordinator paths have dedicated hardening/regression coverage.
- Release/update discovery remains advisory-only and cannot silently rewrite production pins, approve an update, merge a change or deploy production state.

## Current release status

- The usable single-node prototype acceptance gate required by #252 has passed.
- No GitHub release has been published yet.
- `0.1.0` remains the intended first formal usable-prototype release and still requires the publication checklist in `docs/RELEASE_PROCESS.md` on an exact passing release commit.
- `1.0.0` remains the operational baseline target after #46 full platform conformance passes for the profiles claimed by the release.
