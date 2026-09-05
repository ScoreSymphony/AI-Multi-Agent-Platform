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

## Triage cadence

At least weekly, the maintainer should review:

- new and unclassified issues;
- blocked issues and stale hard dependencies;
- milestone scope and progress;
- Dependabot and security findings;
- pull requests without an owner or decision; and
- roadmap statements that no longer match GitHub state.

Milestones close only when their documented exit criteria are satisfied. Dates may be added when there is a real delivery commitment; they must not be invented solely for appearance.

## Releases

Release authority remains with the maintainer. The version policy, evidence requirements and rollback checklist are defined in `docs/RELEASE_PROCESS.md`. Security fixes may use an accelerated private-advisory flow but still require tested remediation and release notes.
