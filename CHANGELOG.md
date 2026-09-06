# Changelog

All notable user-visible changes will be documented in this file.

The project uses semantic versioning once a public prototype is released. Until then, changes accumulate under `Unreleased` and GitHub issues/acceptance gates define the release readiness criteria.

## Unreleased

### Added

- Canonical platform foundations for Tasks, Plans, Steps, Runs, Events, Agents, Agent Teams, models, capabilities/tools, Projects, Workspaces, Files, Artifacts and Results.
- Versioned Control Plane with Web and CLI client paths.
- Authentication, authorization, approvals, secret-reference handling and platform security boundaries.
- Automations, Search, Memory/Knowledge, Verification/Review, Notifications, Chat, Browser and Terminal capabilities.
- Organization/Team/Membership management, practical Task-management metadata and usage/resource accounting foundations.
- Connector and Repository/Git integration, reusable workflow definitions, portable import/export and substantial Template support.
- Supported single-node deployment, optional Control Plane HA/failover and network-capable distributed transport foundations.
- Concrete remote Workspace materialization over the canonical Worker/transport boundary.
- Canonical model-routing profiles with durable immutable revisions and fail-closed schema-version validation.
- Reusable no-paid-service prototype acceptance profiles and machine-readable acceptance reporting.
- Repository governance, ownership, issue-quality automation and supply-chain review.
- Release-manifest validation, compatibility/provenance metadata, fail-closed release gates, an upstream-update PR convention and a security-hotfix runbook.

### Changed

- The project has moved beyond the original architecture/prototype implementation waves; the remaining roadmap is now organized around durable task-bound workflows, distributed deployment, persistence/cross-domain completion, routing-profile hardening, performance and final conformance.
- Issue and pull-request templates reflect the milestone, dependency and canonical-ownership model.
- Project status documentation now distinguishes shipped implementations from post-merge hardening/follow-up work instead of treating first closure as permanent finality.

### Security

- CodeQL, dependency review and scheduled dependency updates are configured.
- Authorization/approval, authentication/session, Workspace isolation, Worker transport/materialization and routing-profile persistence paths have dedicated hardening/regression coverage.

## Current release status

- The usable single-node prototype acceptance gate required by #252 has passed.
- No GitHub release has been published yet.
- `0.1.0` remains the intended first formal usable-prototype release and still requires the publication checklist in `docs/RELEASE_PROCESS.md` on an exact passing release commit.
- `1.0.0` remains the operational baseline target after #46 full platform conformance passes.
