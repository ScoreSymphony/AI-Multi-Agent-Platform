# Governance

## Purpose

This document defines how repository work is selected, reviewed and released. Product and architecture decisions remain governed by `docs/PRODUCT_VISION.md`, `docs/ARCHITECTURE_PRINCIPLES.md` and accepted ADRs.

## Maintainer responsibility

`@ScoreSymphony` is the repository maintainer and default code owner. The maintainer is responsible for:

- milestone and roadmap integrity;
- issue triage and canonical ownership decisions;
- architecture and security review;
- upstream provenance and license decisions;
- release approval and security response; and
- granting future maintainer or reviewer roles explicitly.

Code ownership identifies required reviewers; it does not allow a reviewer to bypass architecture, security or release requirements.

## Planning contract

Every substantive issue must have exactly one label from each dimension:

- `type:*` describes the kind of work;
- `area:*` identifies its canonical owner; and
- `stage:*` identifies the target maturity level.

It must also have one milestone, explicit hard dependencies, non-blocking follow-up integrations, acceptance criteria, required tests and an observable Definition of Done. Accidental placeholders and administrative test issues are excluded from roadmap accounting.

The current issue and milestone state on GitHub is authoritative. The roadmap explains ordering but does not override a newer issue decision.

## Decision process

Routine implementation decisions are made in focused pull requests. A material change to canonical entities, lifecycle ownership, public contracts, persistence ownership, security boundaries, distributed execution or replacement strategy requires an ADR before it is treated as stable.

When reviewers disagree:

1. identify the normative document or invariant involved;
2. record the competing consequences in the issue or ADR;
3. prefer the least coupled, provider-neutral option that preserves the local-first baseline; and
4. let the maintainer accept or reject the documented decision.

## Pull requests

Changes to `main` use pull requests. A pull request must reference its issue, remain focused, pass required checks, resolve review threads and receive the required approval. Force-pushes and deletion of `main` are prohibited.

Security-sensitive and architecture-significant changes require code-owner review. Self-review is useful evidence but does not replace an independent approval when repository rules require one.

Squash merge is the repository merge strategy. The pull-request title becomes the permanent main-history entry, so it must describe the delivered outcome clearly. Merge commits and rebase merges are disabled, and merged head branches are deleted automatically.

## Pull-request lifecycle

Repository work must remain attributable and current:

- new pull requests target initial triage within seven calendar days;
- a draft with no update for 14 days receives a status request;
- work with no response or meaningful progress for 30 days may be marked stale in a maintainer comment;
- after 45 days without a viable completion path, maintainers may close abandoned work with a reason and instructions for reopening;
- security fixes, release candidates and work explicitly waiting on an active hard dependency are exempt from time-based closure while their status remains documented; and
- a contributor may request reopening when the branch is updated against current main and the original outcome is still required.

When a pull request is superseded, the maintainer must compare its remaining unique diff against current main, link the replacement, record whether any unique work remains and then close the older pull request. Conflict resolution must not restore code, tests, documentation or contracts already replaced on main.

Closing stale or superseded work is repository housekeeping, not a rejection of the contributor. Decisions are based on the current canonical contract and demonstrable remaining value.

## Triage cadence

The repository uses the following operating cadence:

- **Weekly:** review new/unclassified issues, pull requests awaiting a decision, Dependabot updates and security alerts.
- **Every two weeks:** reconcile milestone progress, blocked hard dependencies and roadmap statements against current GitHub state.
- **Monthly:** review stale/superseded pull requests, abandoned branches, support trends and dependency update backlog.
- **Quarterly:** review collaborator permissions, CODEOWNERS, branch protection, security reporting, the threat model and governance documents.
- **Before every release:** execute the release checklist, verify provenance/licenses, review unresolved security findings and confirm rollback evidence.

The weekly review includes:

- new and unclassified issues;
- blocked issues and stale hard dependencies;
- milestone scope and progress;
- Dependabot and security findings;
- pull requests without an owner or decision; and
- roadmap statements that no longer match GitHub state.

The maintainer records material decisions in the affected issue, pull request, advisory or ADR. A cadence review does not require a new tracking issue when no action is needed.

Milestones close only when their documented exit criteria are satisfied. Dates may be added when there is a real delivery commitment; they must not be invented solely for appearance.

## Releases

Release authority remains with the maintainer. The version policy, evidence requirements and rollback checklist are defined in `docs/RELEASE_PROCESS.md`. Security fixes may use an accelerated private-advisory flow but still require tested remediation and release notes.
