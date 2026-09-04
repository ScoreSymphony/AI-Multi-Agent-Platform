# Dependency-Driven Implementation Roadmap

> Status baseline: 2026-09-04

This roadmap orders the remaining work toward two distinct outcomes:

1. a genuinely usable, local-first single-node prototype; and
2. the broader operational and distributed platform described by the product vision.

GitHub issue state and the current wording of each issue remain the source of truth. The normative product and architecture baseline is:

- [`PRODUCT_VISION.md`](PRODUCT_VISION.md)
- [`ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md)
- accepted ADRs under [`adr/`](adr/README.md)

## Planning model

Work is grouped into four milestones:

| Milestone | Outcome |
|---|---|
| M1 - Architecture Foundation | Stable canonical domain, contracts and security boundaries |
| M2 - Usable Single-Node Prototype | A new user can install locally, configure a local model, perform useful Agent work and recover state after restart |
| M3 - Operational v1 | Complete product domains, operations, integrations and full conformance |
| M4 - Distributed & Ecosystem | Optional Registry, heterogeneous deployment and HA capabilities |

Labels use three orthogonal dimensions: `type:*` for the kind of work, `area:*` for canonical ownership and `stage:*` for the target maturity level.

Hard dependencies block issue completion. Follow-up integrations and related work do not block unless an issue explicitly says otherwise. Canonical contracts merge before their UI, CLI, adapter or deployment consumers.

## Completed foundation

The main branch already contains the substantive M1 baseline:

- product, repository and provenance foundations: #1, #2, #3;
- canonical domain, replaceable interfaces, kernel and reference executor: #4, #5, #6, #7;
- adapters and provider boundaries: #8, #9, #10, #11, #12, #13;
- identity, authorization, observability and the initial Web shell: #15, #16, #17;
- automations, evaluation and plugins: #18, #19, #20;
- Control Plane, Agents, configuration, transport, authentication and Workspaces: #32, #33, #34, #35, #36, #37;
- CLI and supported single-node deployment: #38, #39;
- Browser, Terminal, starter Agents, accounting and practical Task management: #73, #74, #76, #77, #88.

Closed issues can still have extracted follow-ups. In particular, #17 owns the completed initial shell; progressive domain coverage is now owned by #236 and the prototype gate #252.

## M2 - Usable Single-Node Prototype

The project previously had broad platform acceptance in #46 but no smaller gate proving that the current product is useful before Registry, HA or distributed infrastructure exists. M2 closes that gap.

### Critical prototype lane

```text
closed foundations (#7, #10, #12, #15, #17, #32, #34, #36, #37, #38, #39, #77)
        |
        +--> #72 Task-centric Chat -------------------+
        +--> #86 Verification/Review -----------------+
        +--> #250 First-run and local-model path -----+--> #252 usable-prototype acceptance gate
        +--> #251 Memory/Knowledge lifecycle ---------+
        +--> required progressive UI slices in #236 -+
```

### Required outcome

#252 is the release gate for the prototype. It must demonstrate, without a recurring paid service:

- a clean supported single-node installation;
- first-user authentication and Project/Workspace selection;
- a local or self-hosted model configuration;
- an editable General Assistant;
- Chat or Task execution through canonical APIs;
- one safe capability and a visible result/artifact;
- distinct Approval and Verification behavior;
- a minimal Memory/Knowledge lifecycle;
- actionable degraded states; and
- persistence across restart.

#72, #86 and #236 may deliver the slices consumed by #252 before their broader issues close, provided the consumed contracts are merged, stable and covered by owning tests.

### Explicit non-blockers for M2

The following capabilities are valuable but must not block the usable prototype:

- distributed scheduling beyond local/reference execution (#14);
- external Connectors and hosted Repository integration (#44, #82);
- Notifications, Organizations and cross-scope collaboration (#75, #87, #157, #171);
- Templates and portable distribution (#78, #79, #81);
- full backup, upgrade and release automation (#40, #41, #42);
- heterogeneous deployments and HA (#240, #89).

## M3 - Operational v1

M3 completes the product and operational domains around the proven single-node path.

### Runtime and workflow decisions

- #14 defines Node/Worker scheduling semantics for distributed execution.
- #21 evaluates durable workflow-engine adoption after #14; it remains an ADR/evidence issue and must not introduce Temporal by assumption.
- #214 integrates upstream lifecycle contracts without making an upstream project canonical.

### Product and collaboration domains

- #44 owns external Connector contracts; #82 consumes them for hosted Repository providers.
- #45 expands platform-wide search over canonical resources.
- #75 owns Notifications and user-attention delivery.
- #78 and #79 own reusable Templates and portable Import/Export.
- #87 owns Organization, Team and Membership semantics.
- #157 consumes #87 for safe cross-scope Task moves.
- #171 consumes #75 and #87 for the remaining accounting integrations.
- #236 completes progressive Web UI coverage without creating client-private domain contracts.
- #241 verifies Forge compatibility against platform-owned contracts.

### Operations chain

```text
#39 single-node deployment
        |
        v
#40 backup/restore --> #41 migrations/upgrades --> #42 release/update synchronization
```

The chain may consume Templates/Import-Export where useful, but operational backup must remain distinct from portable resource export.

### Full conformance

#46 remains the final cross-platform and cross-domain conformance suite. It is not the prototype gate. Optional Registry, HA and distributed scenarios belong in conditional profiles and cannot make the reference single-node baseline fail.

## M4 - Distributed & Ecosystem

M4 contains optional expansion work:

- #81 optional Registry/Marketplace for shareable platform extensions;
- #89 Control Plane HA and failover architecture;
- #240 distributed and heterogeneous deployment profiles.

Disabling all M4 components must leave the M2 single-node product usable and the M3 operational baseline valid.

## Recommended execution order

1. Finish the four M2 product gaps: #72, #86, #250 and #251.
2. Integrate only their required Web slices through #236.
3. Run and pass #252; publish the first prototype only after this gate is reproducible.
4. In parallel where ownership is independent, advance #14, #44, #45, #75, #78, #79, #87 and #241.
5. Complete dependent integrations #82, #157, #171 and #214.
6. Drive the operational chain #40 -> #41 -> #42.
7. Close M3 through #46 full conformance.
8. Build #81, #89 and #240 only as optional M4 profiles.

## Consistency rules for every issue

Each implementation issue should contain:

- exactly one canonical owner and no competing private contract;
- an explicit `Hard dependencies` section;
- separate `Follow-up integrations` or `Related work` sections for non-blockers;
- acceptance criteria and required tests proportional to its risk;
- one `type:*`, one `area:*` and one `stage:*` label; and
- the milestone whose outcome the issue is required to satisfy.

When a broad issue contains a smaller milestone-critical slice, the broad issue may stay open after that slice lands. The consuming gate must name the stable contracts it uses and may not silently duplicate the owner issue.
