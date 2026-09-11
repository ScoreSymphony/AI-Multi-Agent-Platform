# Project status

This document is the repository home for curated, point-in-time implementation and integration status.

It is intentionally **not** a second issue tracker, release history or architecture specification:

- GitHub issues and their explicit dependencies are authoritative for individual work-item state.
- Pull-request reviews, required checks and the exact merged branch state are authoritative for merge readiness.
- [`IMPLEMENTATION_ROADMAP.md`](IMPLEMENTATION_ROADMAP.md) owns dependency-driven planning, parallel work lanes and convergence guidance.
- Published [GitHub releases](https://github.com/ScoreSymphony/AI-Multi-Agent-Platform/releases) and their accepted commits/manifest evidence own release history; [`RELEASE_PROCESS.md`](RELEASE_PROCESS.md) governs publication.
- [`PRODUCT_VISION.md`](PRODUCT_VISION.md), [`ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md), accepted ADRs and the relevant domain/contract documents own product and architecture decisions.

Repository documentation/operations owns this status document. Update it at meaningful integration, milestone or release boundaries; do not edit it merely to mirror every issue or pull-request transition. When this document and live GitHub state differ, live GitHub state and merged code win.

## Current integration status

> Curated integration note: 2026-09-11

The repository is in active product-integration, acceptance, hardening and operating-envelope work rather than foundational platform construction. Canonical lifecycle/domain ownership, Control Plane APIs, execution/model/tool/provider boundaries, Agents/Agent Teams, authorization/approvals, Workspaces/files/Artifacts, Memory/Knowledge, Search, Automations, distributed Worker execution, durable Plan/Step coordination, Registry/Marketplace support and release/update foundations already exist in the repository.

Current work is deliberately being developed on focused issue branches and then staged through the shared `integration/active-issue-batch` branch before anything reaches `main`. The corresponding integration pull request is [PR #782](https://github.com/ScoreSymphony/AI-Multi-Agent-Platform/pull/782). Its live PR body and checks are the authoritative source for the exact batch composition.

The #728 staging branch is kept synchronized with the active integration branch at meaningful convergence points. This lets README/status cleanup absorb already-staged cross-issue documentation changes while remaining a late final documentation pass rather than a competing integration branch. At this convergence point, the #728 branch is based directly on integration commit `9fffe722be01acc4f92773a7bbc8c4872c240b65`, including the staged #750 release-gate hardening. Later integration commits must still be incorporated before final closure.

The integration policy is:

1. validate each focused issue branch independently;
2. stage only dependency-clean, conflict-free work into the shared integration branch;
3. resolve cross-issue conflicts on that integration branch rather than independently restoring stale file versions;
4. run the full required repository validation on the combined head;
5. refresh this status summary from the final combined state before the integration branch is proposed for `main`.

Issue #728 is the documentation/status cleanup that established this separation. Its branch should remain a late integration pass so README/status text can describe the combined repository state instead of racing every parallel implementation branch.

For exact current work-item state, use the repository's [open issues](https://github.com/ScoreSymphony/AI-Multi-Agent-Platform/issues?q=is%3Aissue%20state%3Aopen), [open pull requests](https://github.com/ScoreSymphony/AI-Multi-Agent-Platform/pulls?q=is%3Apr%20is%3Aopen), the relevant milestone/dependency declarations and [published releases](https://github.com/ScoreSymphony/AI-Multi-Agent-Platform/releases).

## Historical README status snapshot — 2026-09-07

The following material was moved from `README.md` by #728. It is retained as a dated historical snapshot for provenance and must not be read as current GitHub state.

> Point-in-time status snapshot: 2026-09-07, after closure of #500, #567 and #568 and creation of the focused #579 test-layout continuation

The repository was already in late product integration, acceptance and operating-envelope work rather than foundational platform construction. The usable single-node prototype gate (#252) was complete, and `main` contained the canonical kernel and Control Plane, reference execution, model routing, capabilities/tools, Agents and Agent Teams, authentication and authorization/approvals, Workspaces/files/Artifacts, Memory and Knowledge, Search, Automations, Notifications, Organizations/Teams/Memberships, Chat, Browser and Terminal entry points, reusable workflow definitions, Verification/Review, accounting, durable Connectors, portable import/export, Repository/Git integration, optional HA/failover semantics, extensive Web/CLI coverage, distributed Worker/Workspace execution, the durable Plan/Step coordinator, Registry/Marketplace support and the release/update system.

Recent convergence at that snapshot included:

- **#78 was closed**; Template frontend/documentation reconciliation was no longer an open work lane.
- **#421 was closed via PR #540**; Web and CLI exposed canonical durable workflow progress through the versioned Control Plane. The narrower follow-up **#560** owned safe wait/retry projection semantics, reliable coordinator-driven live refresh and final Web conformance coverage.
- **#439 had substantial planning/replanning implementation merged through PRs #546 and #559**, including platform-owned planning contracts, durable proposals/revisions, #384 handoff and exact planned Step-to-Agent execution bindings. PR **#573** added canonical runtime-evidence-to-replanning bridging but deliberately did not claim #439 complete.
- **#500 was closed**. The portable pressure/admission contract, Linux PSI/swap/zRAM/cgroup provider, observability, authenticated Worker reporting, deployment/doctor integration and bounded semantic benchmark path were accepted. Further real-host pressure/stress evidence belonged to **#440**, not a reopened #500; follow-up work included the read-only observer in PR **#576** and additional #440 benchmark profiles.
- **#501 had a merged Proposal/Specification governance core via PR #541**, including exact-revision Approval binding and idempotent Task conversion. PR **#575** targeted remaining governance recovery/idempotency gaps and proposed to close #501.
- **#502 had a merged provider-neutral repository-intelligence foundation and policy-enforced production wiring**. Dirty-Workspace freshness, candidate evaluation/pilots, plugin/Registry packaging and resource/evaluation work remained open.
- **#440 included substantial single-node, coordination, API-pressure, distributed Worker/Workspace fault, heterogeneous-placement and host-pressure benchmark evidence**, but still owned final operating-envelope, real-host pressure/planner profiles, endurance/stress evidence and regression-budget work.
- **#388 remained a narrow real-host transport acceptance gap**: the network adapter existed, but actual encrypted/authenticated two-host evidence and Artifact/evidence-reference return-path proof were still required.
- **#562 added the broader production-shaped two-VPS/private-tunnel validation**. It could reuse the same hardened topology as #388, but its issue declared **#46 as a hard dependency**, so #562 remained blocked until #46 closed or that dependency was explicitly revised.
- **#566 remained the optional HA productionization follow-up** to #89. It owned a real multi-process/multi-host CoordinationProvider plus shared/replicated durable-state composition and was not to become a hidden dependency of the single-node baseline.
- **#567 was closed via PR #574**. Canonical Task management and reassignment used a supported narrow Task mutation boundary instead of external coupling to private kernel command primitives.
- **#568 was closed via PR #570**. The Python test suite had stable suite categories/markers with path-sensitive tests deliberately preserved where required.
- **#579 was a narrow behavior-neutral continuation of the #568 layout work**, moving a verified-safe Accounting/Usage integration cohort into the canonical suite taxonomy while retaining path-bound conformance tests at stable locations.

At that snapshot there were **10 open issues out of 114 repository issues** (**104 closed; about 91.2% closed by issue count**):

`#46, #388, #439, #440, #501, #502, #560, #562, #566, #579`

The percentage had moved slightly downward because #579 added a narrowly scoped maintenance issue after #568 closed; no merged product capability was lost.

The frontier at that snapshot was split deliberately:

- **Operational-v1/core convergence:** #46 final conformance, #439 planning/replanning closure, #440 remaining performance/operating-envelope evidence and #560 workflow-progress semantics/live-refresh completion. #560 hard-depended on #439 unless that dependency was explicitly revised.
- **Real distributed acceptance:** #388 transport-specific two-host acceptance plus #562 full two-VPS/private-tunnel deployment validation, with #562's #46 dependency still authoritative.
- **Optional ideal-end-state expansion:** #501 Proposal/Specification governance, #502 repository intelligence and #566 production-shaped Control Plane HA.
- **Repository maintainability:** #579 continued stable test-layout migration in a small accounting integration cohort.

Pull-request activity was intentionally treated as more volatile than the issue/dependency snapshot. GitHub issue state, explicit dependencies, current reviews/checks and merged `main` remained authoritative at merge time.

No GitHub release had been published at that snapshot. The release/update machinery existed, but a published release still required the repository release process, validated evidence/manifest and an exact accepted release commit. The operational `1.0.0` baseline remained tied to the supported M3 profile and #46 conformance rather than optional ecosystem or HA features.

## Updating this document

When refreshing status:

- state the snapshot date or integration/release commit when practical;
- summarize meaningful maturity, integration and blocking changes rather than copying every issue transition;
- link to authoritative issues, PRs, milestones, releases or roadmap sections instead of duplicating their full tracking state;
- preserve still-useful historical snapshots under dated headings when traceability matters;
- remove or relabel material that is no longer current rather than leaving ambiguous undated prose;
- do not redefine architecture, dependencies or release acceptance here.
