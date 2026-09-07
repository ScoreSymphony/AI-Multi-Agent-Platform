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
- Canonical Web and CLI durable workflow-progress views backed only by the versioned Control Plane, including Plan revision, Step/dependency state, latest Run attempts, waits/deadlines, retries and reconciliation evidence.
- Platform-owned autonomous planning and bounded-replanning foundations with deterministic/model-backed planner paths, immutable planning proposals/revisions, #384 handoff and planned Step execution bindings that reach the canonical Agent runtime.
- Optional Proposal/Specification governance with versioned Proposal history, immutable Specification revisions/digests, exact-revision Approval binding, idempotent Task conversion, Search/audit integration and Web/CLI surfaces.
- Provider-neutral repository-intelligence baseline capabilities for repository maps, deterministic search, exact source slices, health/index status and exact revision provenance, wired through the existing authorized Repository policy boundary.
- Portable host-pressure and pressure-aware admission foundations, including observability integration, authenticated remote Worker pressure reporting, deployment composition and `platform doctor` visibility without creating a second scheduler.
- Optional replaceable Registry/Marketplace foundations with offline/local discovery, compatibility/integrity/trust validation, canonical owner-domain activation, CLI commands and typed frontend client hooks.
- Canonical model-routing profiles with durable immutable revisions, authorized management, exact-revision assignment, portable provenance and fail-closed schema/history validation.
- Durable Connector persistence with restart-stable Connection, external-resource identity and SyncCheckpoint reconstruction while keeping Search derived.
- Reusable no-paid-service prototype acceptance profiles and a platform-wide conformance framework with fast/integration/release profiles and machine-readable evidence.
- Production-shaped conformance evidence for required reference-release scenarios, optional compatibility claims, Backup/Upgrade/Evaluation release checks and an authenticated cross-layer Agent/Model/Capability/Worker/Workspace/Artifact/Verification vertical slice.
- Deterministic performance tooling covering concurrency sweeps, read/mixed/history/restart workloads, idle footprint, soak/endurance, bounded stress, restart-under-load, transport backpressure/outage/duplicate-delivery behavior, distributed Worker/Workspace faults and heterogeneous capability/resource placement.
- Repository governance, ownership, issue-quality automation and supply-chain review.
- Release-manifest validation, complete compatibility/provenance metadata, fail-closed release gates, advisory upstream discovery, an upstream-update PR convention and a security-hotfix runbook.
- Release-manifest schema v2 with cryptographically bound dependency/artifact provenance, typed gate evidence and complete canonical version-vector compatibility state.

### Changed

- The project has moved beyond the original architecture/prototype implementation waves; the remaining roadmap is now organized around final planning/replanning closure, workflow-progress semantic completion, host-pressure/runtime hardening, measured operating envelopes, real-host distributed acceptance, optional governance/repository-intelligence completion and final platform conformance.
- Ordinary canonical Task/Run execution can use the shared `DistributedRuntime` when advanced deployment is explicitly enabled, while the #39 single-node path remains unchanged when distributed execution is disabled.
- Reference distributed profiles are profile-aware and use the same authenticated Worker reporter/transport contracts for local and remote Workers rather than separate lifecycle models.
- Templates now integrate canonical Workflow, Capability Assignment and Model Routing Policy owner domains instead of keeping those types as unresolved/fail-closed placeholders.
- Registry activation routes through existing plugin/import/template owner domains instead of creating a second installation or mutation authority.
- Accounting integrations now derive Workspace, Node/Worker, Agent/Team, Organization/Team and budget-notification behavior from their canonical source domains while preserving #76 as the accounting authority.
- Planning activation now preserves validated Step execution requirements into the canonical #384/Agent runtime path instead of allowing a planned Agent binding to degrade silently into generic execution.
- Repository intelligence is composed through `RepositoryService` authorization/provider routing rather than reading provider registries or concrete repository implementations directly.
- Host-pressure diagnostics and admission are composed into existing scheduler, observability, Worker protocol and doctor surfaces rather than introducing parallel lifecycle or telemetry ownership.
- Project status documentation now reflects the current open set (`#46, #388, #439, #440, #500, #501, #502, #560, #562`), the closure of #78/#421, and the absence of open pull requests at the 2026-09-07 snapshot.

### Fixed

- Model-routing profile management and portability now preserve immutable historical response semantics, monotonic chronology, exact assignment authorization across Template/import consumers, revision/schema consistency and safe compensation when other canonical resources may still reference a profile.
- Canonical Node/Worker `updated_at` semantics now represent state changes independently from heartbeat/liveness timestamps and remain monotonic across re-registration, persistence and legacy restore.
- Connector state now survives normal single-node/server restart and authorized Search reconstruction without promoting Search to canonical persistence.
- Release provenance/compatibility validation now fails closed on incomplete dependency digests, malformed source commits, unbound evidence, compatibility revision mismatches and incomplete version-vector comparisons.
- Post-planning-merge import, typing and canonical-event regressions were repaired so planning uses the public authorization contract and canonical event payload representation.
- The main branch Ruff formatting regression introduced during parallel release work was corrected without behavioral changes.

### Security

- CodeQL, dependency review and scheduled dependency updates are configured.
- Authorization/approval, authentication/session, Workspace isolation, Worker transport/materialization, routing-profile assignment/compensation, Template/Registry activation, planning, repository-intelligence access and durable coordinator paths have dedicated hardening/regression coverage.
- Host-pressure reporting trusts remote pressure evidence only after the canonical Worker authentication boundary and keeps provider-private host metadata out of canonical user-visible projections.
- GitHub Actions dependencies used by repository workflows continue to be updated through reviewed dependency changes rather than silent pin drift.
- Release/update discovery remains advisory-only and cannot silently rewrite production pins, approve an update, merge a change or deploy production state.

## Current release status

- The usable single-node prototype acceptance gate required by #252 has passed.
- No GitHub release has been published yet.
- `0.1.0` remains the intended first formal usable-prototype release and still requires the publication checklist in `docs/RELEASE_PROCESS.md` on an exact passing release commit.
- `1.0.0` remains the operational baseline target after #46 full platform conformance passes for the profiles claimed by the release.
