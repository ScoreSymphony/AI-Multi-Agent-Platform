# License Policy

This repository is intended to remain MIT-licensed at the project level. Third-party software may only be integrated in ways that keep upstream obligations explicit, preserve required notices, and maintain replaceable platform boundaries.

Open source does **not** automatically mean that source is safe to copy into this repository. License compatibility, provenance, transitive obligations, architecture impact, and an exit strategy must be reviewed before adoption.

## Integration categories

Every third-party component or upstream influence must be classified before integration.

| Category | Meaning | License treatment | Provenance requirement | Update / review expectation |
| --- | --- | --- | --- | --- |
| **Protocol/specification integration** | Implement a public protocol/specification without importing upstream runtime source. | Verify the specification terms and any relevant patent/trademark or code-sample terms. Do not assume protocol documentation grants source-code rights. | Record specification/upstream URL, version, terms/license, platform boundary, compatibility constraints. | Review when protocol revisions affect compatibility; breaking contract changes require explicit architecture review. |
| **Library dependency** | Consume an upstream package through normal dependency management. | Verify the direct package license and review material transitive license obligations before making it required. | Record canonical upstream, dependency constraint/pin, license, adapter/boundary, baseline status, review date. | Update through a dedicated dependency PR when architecture-significant or required in production; run contract/regression tests. |
| **External self-hosted service** | Communicate with separately deployed software through a stable API/protocol. | The service retains its own license; verify deployment/use rights and redistribution implications for images/bundles. | Record canonical upstream, deployed version/commit, license, API boundary, resource/deployment constraints, exit strategy. | Monitor strategically important services; breaking API or deployment changes require explicit review. |
| **Adapter integration** | Platform-owned translation layer around an upstream library/service. | Adapter code is project-owned; upstream library/service keeps its own license. | Record both the adapter path and upstream interface/version/license. | Adapter contract tests are required for material upstream changes. |
| **Vendored source** | Store an upstream source snapshot directly in this repository. | Allowed only after compatibility is established. Preserve required copyright/license notices and any NOTICE files. | Record exact source revision, local path, retained notices, modification state, comparison/update procedure. | Dedicated PR, reproducible upstream diff, retained upstream tests where useful, and explicit review. |
| **Forked source** | Maintain a derivative codebase with explicit upstream tracking. | Preserve upstream license/notice obligations and mark project modifications where appropriate. | Record fork origin, divergence revision, local/fork location, modification policy, sync/cherry-pick strategy. | Periodic upstream review is mandatory while the fork is maintained; significant divergence must be documented. |
| **Selective code port** | Copy or adapt specific upstream files, functions, algorithms, or implementation fragments. | Treat copied/adapted code as upstream-derived source. Preserve applicable notices; do not relabel it as purely project-owned MIT code. | Record exact source file/revision, destination, modification summary, license, notices, and rationale. | Review upstream fixes/security changes affecting the port; maintain traceable comparison history. |
| **Reference-only influence** | Learn architecture or behavior from upstream without copying source or protected expression. | No source is imported, but license and provenance should still be noted when the upstream materially influenced an architecture decision. | Record reference URL/project and the ADR/design area influenced when architecture-significant. | No source-sync requirement; revisit only when the reference materially affects an architectural decision. |

A component may use more than one category. For example, an external service can also have a platform-owned adapter. Record the primary integration category and any secondary categories that materially change obligations.

## Rules for source inclusion

Third-party source must not be copied, vendored, forked, or selectively ported into this repository until all of the following are known and recorded:

- canonical upstream repository or source location;
- exact version, tag, commit, or source revision used;
- upstream license and required copyright/license/NOTICE obligations;
- whether the imported source is modified;
- source origin and destination path in this repository;
- responsible platform adapter/component boundary;
- transitive or bundled license concerns relevant to redistribution;
- update/comparison procedure;
- exit/replacement strategy when architecture-significant.

Source with terms that cannot coexist with this repository's MIT distribution model must remain an external dependency/service or be rejected. Permissive licenses retain their own notice and attribution obligations and are not silently relicensed as MIT.

Architectural convenience is never sufficient reason to cross a license boundary.

## Marking modified upstream source

When upstream-derived source is modified and the upstream license or project convention requires or benefits from modification marking:

1. retain the original copyright/license header or adjacent license file;
2. add a concise project modification note without deleting upstream attribution;
3. record the upstream revision and local modification summary in the provenance record;
4. keep a reproducible path for comparing the local copy with the recorded upstream revision.

Do not add misleading headers that imply project ownership of upstream-authored code.

## Dependency and transitive-license review

Before a library or bundled service becomes a required production component:

- verify the direct upstream license;
- identify material bundled/transitive dependencies when their licenses can affect redistribution, deployment, linking, or notice obligations;
- record unresolved concerns in the adoption review;
- block promotion to `approved` or `integrated` if compatibility is unclear.

Standard development/build tooling may be tracked separately from architecture-significant upstreams, but it remains subject to its package license and dependency-management obligations.

## Provenance

Every approved or integrated architecture-significant third-party component must have a provenance record containing at least:

- upstream project name and canonical repository URL;
- upstream license and license verification date;
- exact version/tag/commit or dependency/deployment pin;
- integration category and status;
- whether source was modified;
- local location of vendored/modified code where applicable;
- required notices;
- platform adapter/boundary;
- update procedure;
- last review date;
- known compatibility constraints;
- exit/replacement strategy;
- baseline/optional status and recurring paid-service requirement.

The canonical architecture-upstream inventory is `docs/UPSTREAMS.md`. The machine-readable starting template is `upstream/PROVENANCE_TEMPLATE.yaml`.

## Adoption review

Before adopting a new architecture-significant upstream, complete `docs/UPSTREAM_ADOPTION_CHECKLIST.md`. The review must consider functional and architecture fit, replaceability, licensing, maintenance status, security, resource and deployment footprint, dependency footprint, API stability, migration/exit strategy, and whether the capability is simpler to implement inside the platform.

## Change control

A pull request that adds or materially changes a third-party integration must:

- update `docs/UPSTREAMS.md` when the component becomes approved/integrated or its recorded state changes;
- identify integration category/categories;
- record the verified upstream version/commit and license;
- state whether source is copied, modified, forked, ported, or only referenced;
- preserve required notices;
- include adoption-review evidence for a new architecture-significant upstream;
- explain the chosen integration boundary and replacement path;
- preserve platform-owned canonical contracts.

If license compatibility is unclear, source inclusion is blocked until it is resolved. Genuine ambiguity should receive appropriate legal review rather than being resolved by assumption.

## Architecture-significant upstream changes

An upstream change is architecture-significant when it changes canonical contract assumptions, lifecycle semantics, persistence ownership, security boundaries, deployment topology, distributed-node behavior, required resources, or platform replaceability.

Such changes must not be silently substituted. They require explicit PR review and an ADR when the platform architecture or canonical contracts must change.

## Upstream updates and periodic review

Upstream updates follow `docs/UPSTREAM_UPDATE_WORKFLOW.md`. Strategically important external services, forks, vendored sources, selective ports, and other architecture-significant upstreams must declare a review/update method in the registry. The process must support manual checks now and automation later without automatically accepting upstream changes.
