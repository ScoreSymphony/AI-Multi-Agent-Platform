# Dependency-Driven Implementation Roadmap

This document describes the repository's **dependency and convergence model**. It is not a live issue ledger. GitHub issue/dependency state, pull-request checks and merged code remain authoritative for individual work items, while [`STATUS.md`](STATUS.md) records the curated product-integration snapshot.

The normative product and architecture baseline remains:

- [`PRODUCT_VISION.md`](PRODUCT_VISION.md)
- [`ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md)
- [`DOMAIN_MODEL.md`](DOMAIN_MODEL.md)
- [`CONTRACTS.md`](CONTRACTS.md)
- accepted ADRs under [`adr/`](adr/README.md)

Issue numbers are identifiers, not an implementation sequence. Explicit hard dependencies and the current merged repository always take precedence over a planning document.

## Current convergence state

The project is no longer in foundational platform construction. The canonical lifecycle/domain model, versioned Control Plane, Web and CLI clients, provider boundaries, Agents/Agent Teams, model routing, Capability/Tool execution, Projects/Workspaces, persistence, security/Approvals, Verification, Templates/portability, Search/Memory/Knowledge, distributed Worker execution, observability, backup/restore, upgrade/migration and release/conformance foundations are implemented on `main`.

The current release path is therefore:

```text
merged product baseline
        |
        v
whole-product readiness audit
        |
        v
resolve any release-blocking findings
        |
        v
freeze exact release candidate
        |
        v
exact-candidate conformance / CI / packaging evidence
        |
        v
formal publication
```

The first formal publication has not happened yet. Until an exact candidate passes the terminal readiness and release gates, package/release metadata remains pre-publication and product claims stay conservative.

Optional/advanced profiles do not become baseline dependencies merely because their implementation exists. High availability, distributed/heterogeneous deployment profiles and other optional integrations retain the role/stability and evidence requirements defined by [`FEATURE_CLASSIFICATION.md`](FEATURE_CLASSIFICATION.md) and their owning documentation.

## Dependency rules

Use these rules when sequencing remaining or newly discovered work:

1. **Canonical owner before clients.** A Web, CLI, Search, Template, Marketplace or portability surface depends on the owning domain contract; clients must not invent lifecycle state.
2. **Contracts before concrete adapters.** Optional orchestrators, executors, model gateways, tool transports and external systems implement platform-owned seams rather than defining them.
3. **Persistence/recovery with lifecycle changes.** New durable state is incomplete until restart, replay/idempotency and migration/backup implications are addressed.
4. **Security before privileged reachability.** Authentication, authorization, Approval, secret handling and side-effect boundaries must exist before a privileged product action is exposed.
5. **Public API before cross-client parity.** Web and CLI consume the versioned Control Plane and should converge on the same canonical resources, commands, status values and error contracts.
6. **Implementation before acceptance claims.** Unit/integration coverage proves components; product/release acceptance must exercise the composed public path.
7. **Optional profiles stay optional.** Advanced deployment or third-party integrations cannot become hidden requirements of the local/reference baseline.
8. **Exact evidence before release claims.** A PASS, compatibility claim or benchmark applies only to the exact commit/profile that produced the retained evidence.

## Parallel work lanes

Parallel work is safe when ownership and write sets are distinct. Typical low-collision lanes include:

- documentation/product-copy reconciliation;
- isolated provider/adapter compatibility work;
- frontend surface work against an already stable API;
- benchmark/evidence collection that does not change runtime semantics;
- repository hygiene that does not move active ownership boundaries.

Sequence work when two changes touch the same canonical owner, persistence schema, Control Plane command/resource contract, authorization boundary, frontend routing/composition root or release-critical configuration.

Shared integration branches may collect independent work, but they do not replace per-issue dependency discipline or exact-head CI. Reconcile documentation against the combined head only after the relevant implementation has converged.

## Release-readiness planning

During release-readiness work, classify each new finding as one of:

- **release-blocking correctness/security/data/recovery/product gap** — fix before candidate freeze and rerun the affected audit;
- **documentation mismatch that changes operator/user behavior** — correct before documentation acceptance;
- **non-blocking polish/maintenance debt** — record explicitly without weakening the release claim;
- **optional-profile limitation** — keep visibly scoped to that profile and its maturity level.

Do not convert an unresolved blocker into a "known limitation" merely to finish the release. Conversely, do not make an optional/experimental capability a baseline blocker unless the release explicitly claims that profile.

## Progress interpretation

Issue counts, PR counts and historical wave numbers are not measures of product completion. The useful questions are:

- does the claimed canonical end-to-end path work?
- do Web, CLI and API agree on the same state?
- can a clean local/reference deployment be installed, operated, recovered and upgraded?
- are optional integrations clearly optional and replaceable?
- are security and data-integrity boundaries enforced?
- do documentation and release claims match the exact candidate?

Current point-in-time status belongs in [`STATUS.md`](STATUS.md) and the live GitHub tracker rather than duplicated dated issue lists in this roadmap.
