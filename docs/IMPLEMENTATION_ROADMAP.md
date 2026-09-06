# Dependency-Driven Implementation Roadmap

> Status baseline: 2026-09-06

This roadmap describes the remaining work from the current `main` branch toward the ideal end-state platform. It no longer treats the early prototype lanes as future work: the usable single-node prototype acceptance gate (#252) has passed, and most originally planned M1/M2/M3 platform domains now have concrete implementations.

GitHub issue state and the current wording of each issue remain the point-in-time source of truth. The normative product and architecture baseline remains:

- [`PRODUCT_VISION.md`](PRODUCT_VISION.md)
- [`ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md)
- accepted ADRs under [`adr/`](adr/README.md)

## Current maturity

### Reached

The repository now has a broad canonical platform baseline covering:

- product/repository/provenance foundations;
- canonical Task/Plan/Step/Run/Event domain and Task/Run kernel;
- replaceable orchestration, execution, model, capability/tool and persistence/provider contracts;
- reference execution plus Hermes, Forge and LiteLLM compatibility paths;
- Control Plane, authentication, authorization/approvals and secret-reference handling;
- Agents and Agent Teams;
- Projects, Workspaces, Files, Artifacts, Memory and Knowledge;
- Automations, global Search, Verification/Review and Notifications;
- Browser, Terminal, Chat, Web UI and CLI client paths;
- Organizations/Teams/Memberships and practical Task-management metadata;
- accounting foundations and resource-attribution integrations;
- Connectors and Repository/Git integration;
- reusable workflow definitions and portable import/export;
- supported single-node deployment plus optional Control Plane HA/failover;
- a reusable no-paid-service single-node prototype acceptance gate (#252).

Closed issues may still have extracted follow-up issues. A closed first implementation is not treated as proof that every later cross-domain or hardening concern is complete.

### Still open

As of this snapshot, 17 issues remain open:

`#42, #46, #78, #81, #171, #240, #384, #414, #416, #421, #439, #440, #443, #444, #445, #446, #447`.

Only one pull request is currently open: draft PR #386 for #240 distributed/heterogeneous deployment profiles.

## Remaining dependency lanes

### Lane A — Model-routing profile completion

The canonical routing-profile core is implemented and #441/#442 hardening has landed. The remaining work is a compact follow-up cluster:

```text
routing-profile core complete
        |
        +--> #443 authorized management endpoints / real #15 composition
        +--> #444 monotonic revision chronology
        +--> #445 assignment coverage beyond Agents
        +--> #446 provenance immutability / schema-history consistency
        +--> #447 deletion/compensation reference safety
```

These issues are intentionally separate so repository invariants, Control Plane exposure and cross-domain assignment safety do not become one oversized patch. They can be advanced largely in parallel where they do not touch the same persistence invariants.

### Lane B — Durable task-bound workflows

ADR 0008 / #21 selected a narrow platform-owned durable coordination layer rather than adopting Temporal/DBOS/Forge workflow state as canonical authority.

```text
#384 durable Plan/Step coordinator
        |
        +--> #421 Web/CLI workflow-progress surfaces
        +--> #439 autonomous goal decomposition + bounded replanning
        |
        +--> #440 workflow performance profiles
```

#384 is the central dependency. It owns restart-safe Step progression, fan-out/fan-in, waits, retries, cancellation and reconciliation while preserving canonical Task/Plan/Step/Run/Event ownership.

#421 must consume only #384's backend-neutral Control Plane projections. #439 may generate/revise canonical Plans but must leave durable execution to #384. The workflow portions of #440 become meaningful after #384 is stable.

### Lane C — Distributed and heterogeneous deployment

#14 canonical Node/Worker scheduling semantics and #35 network-capable message transport are complete. #433 concrete remote Workspace materialization is also merged.

The current deployment lane is therefore:

```text
#14 + #35 + #36 + #37 + #433 complete
                |
                v
       #240 advanced deployment profiles
          (draft PR #386)
                |
                +--> #440 distributed/load profiles

#414 Node/Worker canonical state-change timestamps
      can progress independently of most #240 packaging work
```

#240 must finish real deployable multi-process/cross-host composition and acceptance without creating a second Worker, Workspace or scheduler model. #414 fixes canonical modification-time semantics so Search/observability do not overload heartbeat time.

### Lane D — Persistence and cross-domain completion

Two remaining issues close real production-shaped persistence/integration gaps:

- **#416 Connector persistence:** make canonical Connector/Connection/ExternalResourceReference/SyncCheckpoint state restart-durable in the normal runtime while keeping Search derived.
- **#171 accounting integrations:** close the remaining configured-deployment wiring, unavailable-vs-zero resource semantics, route protection and explicit combined acceptance regressions.

These can progress independently of #384 and #240 except where shared Node/Worker semantics from #414 affect accounting timestamps or resource metadata.

### Lane E — Template completion

#78 has a substantial implementation but remains open because later canonical domains exposed follow-up gaps.

The remaining work includes:

- rollback/compensation for capability-assignment creation during composite apply;
- `workflow_plan` integration through the canonical reusable Workflow domain;
- create-from-existing support for newer canonical resource types;
- Web/API exposure for those export paths;
- final documentation and acceptance reconciliation.

#78 should close before #81 becomes a serious distribution target, because the optional Registry/Marketplace must distribute canonical portable assets rather than incomplete Template-private substitutes.

### Lane F — Performance, release and full conformance

```text
#440 performance/load/scalability evidence
#42  release/update/upstream synchronization
          \      /
           \    /
            #46 full platform conformance
```

These issues may be developed incrementally now, but final claims must consume the mature owning domains rather than fabricate acceptance-only shortcuts.

- **#440** establishes measured operating envelopes, regression baselines, stress/soak profiles and safe-degradation evidence.
- **#42** turns the existing provenance/update policy into a repeatable release and upstream-synchronization system.
- **#46** remains the final end-to-end architecture and product acceptance standard across reference, adapter, distributed, security, recovery and client profiles.

The prototype acceptance gate #252 is already complete and remains the smaller regression gate for the ordinary single-node path. #46 must not replace it with a heavier mandatory environment.

### Lane G — Optional Registry / ecosystem

#81 remains intentionally optional.

It depends on the already completed plugin/import-export foundations plus final Template completion. The platform must remain fully operable with the Registry disabled or absent.

## Recommended execution order from current `main`

The remaining work should be prioritized by dependency leverage rather than issue number:

1. **Finish the routing-profile follow-ups #443–#447** while their newly implemented domain is still fresh and isolated.
2. **Advance #384 durable Plan/Step coordination** as the largest remaining canonical runtime capability.
3. **Finish #240 / PR #386** now that #433 remote Workspace materialization is merged; run the real deployable Worker acceptance path before closing it.
4. **Complete #414, #416 and #171** as independent correctness/persistence lanes.
5. **Close #78** using the canonical workflow/capability-assignment domains already merged.
6. **Build #421 and #439 after #384 exposes stable contracts.**
7. **Establish #440 performance evidence** progressively: single-node now, workflow after #384, distributed after #240.
8. **Complete #42 release/update mechanics** and reconcile the first formal release target.
9. **Drive #46 to final platform conformance** using the accumulated subsystem evidence rather than a parallel test-only stack.
10. **Build #81 only as an optional ecosystem layer** after the portable/template/plugin contracts it distributes are stable.

## Parallel work that is safe now

A practical high-throughput split is:

| Track | Issues | Notes |
|---|---|---|
| A | #443–#447 | Routing-profile follow-up cluster; coordinate persistence-touching changes |
| B | #384 | Durable workflow coordinator |
| C | #240 | Distributed deployment / PR #386 |
| D | #414 | Node/Worker update timestamp semantics |
| E | #416 | Durable Connector persistence |
| F | #171 | Accounting residual integrations |
| G | #78 | Template follow-up completion |
| H | #440 | Start deterministic single-node benchmark foundation; defer workflow/distributed profiles until dependencies land |
| I | #42 | Release/update tooling can progress without blocking runtime lanes |

#421 and #439 should not outrun #384's canonical contracts. #81 should not outrun #78. #46 can accumulate fixtures and invariant checks but should be the convergence lane, not the owner of missing production behavior.

## Release interpretation

The planned `0.1.0` prerequisite (#252) has passed, but no GitHub release is published yet. A formal prototype release therefore still requires the publication checklist in [`RELEASE_PROCESS.md`](RELEASE_PROCESS.md), current changelog/provenance and an exact passing release commit.

The planned `1.0.0` operational baseline remains tied to #46 full conformance. Optional Registry and other ecosystem features must not become hidden prerequisites for the local/self-hosted baseline.

## Consistency rules for every remaining issue

Each implementation must continue to preserve:

- exactly one canonical owner and no competing private contract;
- explicit hard dependencies separated from follow-up integrations;
- provider/model/hardware/deployment neutrality in canonical state;
- self-hostable reference behavior without a recurring paid AI/API dependency;
- security and verification as authoritative platform boundaries rather than planner/orchestrator hints;
- restart/deduplication semantics where durable state is involved;
- backend-neutral Control Plane projections for Web/CLI clients;
- tests proportional to the failure mode being closed;
- no acceptance-only bypass that hides a production integration gap.

When this roadmap and a GitHub issue diverge, the current issue wording and merged code are authoritative until the roadmap is refreshed again.
