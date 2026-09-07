# Dependency-Driven Implementation Roadmap

> Point-in-time baseline: 2026-09-07, after closure of #500, #567 and #568 and creation of #579

This roadmap describes the remaining work from current `main` toward the operational v1 baseline and the wider ideal end state. The project is no longer in foundational platform construction. Most canonical domains, runtime boundaries, client surfaces, distributed execution building blocks and operational foundations are implemented; remaining work is concentrated in final planning/client convergence, performance and real-host evidence, platform conformance, optional end-state extensions and narrowly scoped repository maintenance.

GitHub issue state, explicit issue dependencies, current issue comments, pull-request reviews/checks and merged code remain the point-in-time source of truth. The normative product and architecture baseline remains:

- [`PRODUCT_VISION.md`](PRODUCT_VISION.md)
- [`ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md)
- accepted ADRs under [`adr/`](adr/README.md)

Issue numbers are identifiers, not an implementation sequence. Explicit hard dependencies take precedence over this planning view whenever they differ.

## Current snapshot

At this snapshot there are **10 open issues out of 114 repository issues**:

`#46, #388, #439, #440, #501, #502, #560, #562, #566, #579`

That is **104 closed / 114 total = about 91.2% closed by issue count**.

The raw percentage is bookkeeping only. It moved slightly downward because #579 was created as a focused behavior-neutral follow-up after #568 closed; no merged product capability was lost. Issue scope is highly uneven, and several remaining issues are optional profile owners, evidence owners or maintenance work rather than missing foundational capability.

Pull-request state changes faster than issue/dependency state and is therefore not used as the primary roadmap structure. Relevant active PRs are referenced only where they materially affect a lane. Before any merge, re-check current `main`, dependency state, mergeability, reviews and every required protected-branch check on the exact final head.

## Recently completed convergence work

### #500 — host-pressure/admission runtime contract

#500 is closed. The accepted platform now includes:

- OS/hardware-neutral pressure snapshots and normalized pressure states;
- deterministic pressure-aware admission before reservation without creating a second scheduler;
- protected-headroom policy;
- optional Linux PSI/swap/paging/zRAM/cgroup/filesystem/descriptor evidence;
- #16 observability integration;
- authenticated/freshness-aware remote Worker pressure reporting;
- distributed deployment and `platform doctor` integration;
- bounded semantic benchmark coverage.

Further real-host pressure, OOM-containment, soak, stress and measured operating-envelope evidence belongs to #440. Current #440 follow-ups such as the read-only pressure observer do not reopen #500.

### #567 — supported canonical Task mutation boundary

#567 is closed via PR #574. Canonical Task-management and Task-reassignment services now use a narrow supported mutation boundary instead of reaching into private `PlatformKernel._commit_task_command()` internals.

The accepted boundary preserves #88/#157 behavior, terminal lifecycle invariants, idempotency, provenance, event ordering and audit while keeping arbitrary low-level command/event construction private to the kernel. Regression/architecture coverage protects against reintroducing the private coupling.

### #568 — stable test-suite layout

#568 is closed via PR #570. The repository now has stable suite categories and pytest markers while preserving path-sensitive historical tests where required. CI, adapter integration paths and benchmark locations were updated without changing production semantics.

This does not mean every safe historical root test must remain at root forever. Small behavior-neutral follow-ups may continue the migration when their path/import/conformance constraints are explicitly verified. #579 is the current example.

## Remaining work by ownership lane

### Lane A — #439 autonomous planning/replanning closure

#439 remains the highest-weight open core integration owner.

Already merged:

- provider-neutral planning proposals/revisions;
- deterministic and model-backed planner paths;
- DAG/reference/satisfiability validation;
- durable planning state and restart behavior;
- Control Plane planning commands/projections;
- #384 activation/handoff;
- bounded-replanning foundations;
- exact planned Step-to-Agent runtime binding and planned execution context reaching AgentRuntime.

PR #573 adds a platform-owned bridge from canonical runtime evidence into bounded replanning. It covers terminal Run failure, retry exhaustion and Verification outcomes with idempotent evidence fingerprints, stale-evidence rejection and explicit replanning telemetry.

PR #573 deliberately does **not** claim #439 complete. Its own follow-up notes still identify at least:

- authorization-aware/restart-safe activation after terminal Task failure;
- running-Step supersession semantics;
- additional server-owned inventory authority hardening;
- provider configurability;
- #19 planner evaluation coverage.

The intended completed loop remains:

```text
Goal/Task
  -> validated canonical Plan
  -> selected canonical Agent/Team + capability/model requirements
  -> #384 durable Step execution
  -> canonical Run/Verification/runtime evidence
  -> bounded evidence-driven replanning
  -> immutable replacement Plan revision
  -> #384 execution
```

#439 must not absorb #384 coordination, #14 scheduling, #10 model routing, #12 capability ownership, #15 Approval or #86 Verification.

### Lane B — #440 performance/load/stress/scalability closure

#440 is a mature benchmark program, not a missing benchmark foundation.

Merged evidence already includes:

- deterministic single-node lifecycle and concurrency sweeps;
- read-heavy/mixed/history/restart workloads;
- persistence growth/reopen evidence;
- idle/soak/bounded stress profiles;
- Control Plane restart under load;
- transport backpressure/outage/duplicate-delivery evidence;
- Plan/Step linear, fan-out/fan-in, retry/wait and contention profiles;
- authenticated Control Plane API/session/authorization/pagination pressure;
- distributed Worker/Workspace scale and fault-under-load profiles;
- Worker loss/rejoin and Workspace failure/recovery evidence;
- heterogeneous capability/resource/GPU/label/network placement benchmarks;
- #500 semantic pressure/admission benchmark coverage.

Current #440 work includes additional real-host pressure observation and deterministic HA-failover benchmark evidence. These extend the evidence program without changing ownership: #440 measures behavior and operating envelopes, while #500 remains the completed pressure runtime contract and #566 remains the owner of production-shaped multi-instance HA.

High-value remaining #440 work is mainly:

- real network/cross-host operating-envelope measurements where reproducible;
- controlled dedicated-host memory/paging pressure and bounded OOM-containment evidence where safe;
- release-sized comparable 1/10/50/100+ sweeps and longer endurance runs;
- measured operating envelopes and justified regression/noise budgets;
- planner/replanning overhead profiles once #439 stabilizes;
- optional real HA load/failover profiles once #566 provides a production-shaped HA path.

Correctness, security, Verification and durability remain hard benchmark invariants.

### Lane C — #560 workflow-progress semantic/live-refresh completion

#421 is closed; #560 owns the narrower client/projection completion work:

- safe canonical Approval/Event/external-job wait context;
- explicit active/resolved/expired/rejected/cancelled wait semantics;
- explicit retry-scheduled vs retry-exhausted semantics;
- stable attempt/budget evidence;
- reliable Web refresh for coordinator-only transitions;
- production-shaped Web and CLI parity;
- maintained #46 client-conformance evidence.

The architecture remains:

```text
Web / CLI
  -> versioned Control Plane
  -> canonical Plan/Step coordination projection
  -> #384 coordinator
```

No client shadow state or direct coordinator persistence access is allowed.

**Dependency status:** #560 currently declares #439 as a hard dependency. PR #577 proposes to close #560, but an implementation PR does not erase an issue dependency. Under repository dependency discipline, #577 must not be considered dependency-clean for merge until either:

1. #439 closes first; or
2. #560's hard dependency is explicitly revised with a justified issue update.

Preparatory implementation may exist on a branch before the dependency is satisfied; merge/closure is the constraint that must remain dependency-clean.

### Lane D — #46 final platform conformance

#46 remains the core convergence owner and should be the last core issue to close for the claimed operational profile.

The repository already has a strong continuous vertical including Worker-produced Artifact -> Verification return paths. #46 should continuously accumulate evidence, but final closure should wait for the exact release/profile claim.

Final audit should consume, where applicable:

- #439 full planning/replanning loop;
- #560 workflow-client parity/live semantics;
- #440 release/operating-envelope evidence required by the claimed profile;
- the completed #500 pressure-aware runtime contract only when the claimed profile enables it;
- the completed #567 kernel encapsulation invariant;
- #388/#562 evidence only for releases claiming real cross-host distributed compatibility;
- #566 evidence only for releases claiming real multi-instance HA.

Optional features must report `supported`, `disabled`, `unsupported` or `not-run` explicitly rather than silently blocking the baseline or creating false compatibility claims.

### Lane E — real-host distributed acceptance: #388 and #562

These issues can reuse infrastructure, but their dependency states are not identical.

#### #388 transport-specific acceptance

The network-capable MessageTransport implementation already exists. Remaining acceptance requires:

- actual two-host encrypted/authenticated transport;
- dispatch and result retrieval across the real network boundary;
- restart/reconnect without changing canonical Worker identity;
- explicit proof that canonical Artifact/evidence references survive the return path.

#388 is ready when the hardened two-host test environment is available.

#### #562 full two-VPS private-tunnel acceptance

#562 broadens the same physical topology into production-shaped distributed deployment validation:

```text
Host A: Control Plane / scheduler
        |
        | private authenticated tunnel
        v
Host B: remote Node / Worker
```

It owns:

- private tunnel/network exposure validation;
- authenticated registration and heartbeat;
- capability/resource-based remote dispatch;
- real result/reconciliation;
- deliberate tunnel interruption;
- liveness expiry and no new placement on unreachable Worker;
- tunnel restoration/re-registration;
- independent Worker restart;
- sanitized acceptance evidence.

**Current dependency status:** #562 explicitly lists #46 as a hard dependency. Therefore #562 remains blocked until #46 closes or that dependency is explicitly revised.

Preparing reusable private-network infrastructure is acceptable, but do not claim #562 issue execution/acceptance complete before its dependency is resolved.

If the project wants #562 evidence to feed the final pre-closure #46 audit, the #46 dependency on #562 must be revised explicitly rather than treated as an implicit exception.

### Lane F — #501 optional Proposal/Specification governance

PR #541 already delivered the main governance foundation:

- versioned Proposal intake;
- immutable Specification revisions and stable digests;
- exact-revision Approval binding;
- stale-Approval invalidation semantics;
- restart-safe/idempotent Specification -> canonical Task conversion;
- direct Task creation remains supported;
- exact approved-revision planning input;
- Control Plane, Search, audit, Web and CLI surfaces;
- persistence/recovery coverage.

PR #575 targets remaining verified recovery/idempotency gaps and declares `Closes #501`.

Until that work is actually merged and #501 is closed, #501 remains an open optional lane. The direct Task path must remain first-class regardless of whether #501 closes.

### Lane G — #502 optional repository intelligence

Already merged:

- provider-neutral capability taxonomy/foundation;
- deterministic repository map/text-search/source-slice/health/index-status baseline;
- exact immutable revision provenance;
- normal CapabilityRegistry fallback semantics;
- production wiring through the authorized RepositoryService boundary.

Remaining high-value work:

- dirty Workspace/source-snapshot freshness and provenance;
- fresh source/license/security/maintenance verification of realistic provider candidates;
- isolated provider pilot when a candidate actually passes review;
- plugin packaging and optional Registry metadata;
- resource/evaluation evidence against Git/ripgrep/LSP baseline;
- planner/reviewer context-funnel integration after stable canonical contracts.

Fallback remains mandatory:

```text
optional intelligence unavailable/stale
  -> Git + ripgrep + LSP / canonical Repository baseline
  -> work continues
```

### Lane H — #566 optional production-shaped Control Plane HA

#89 established HA semantics and deterministic reference fixtures. #566 owns productionization for a real independent-process/host profile.

It requires:

- at least one self-hostable `CoordinationProvider` usable across independent processes/hosts;
- atomic acquire/renew/release and monotonic fencing epochs;
- backend-owned lease expiry and stale-authority rejection;
- health/readiness and fail-closed coordination outage behavior;
- a durable-state composition safe for active/passive promotion;
- shared/replicated canonical state rather than process-local objects or unsupported shared-file SQLite;
- real active/standby process replacement and failover acceptance;
- preservation of auth/session/revocation, event cursors, idempotency, Workers, Automations and reconciliation state;
- optional two-host acceptance;
- no paid hosted coordination requirement.

#566 is architecturally important for the full ideal end state but **optional for ordinary single-node operational v1**.

Deterministic process-local HA benchmark evidence under #440 remains useful but must not be misrepresented as satisfying #566's independent-process/host productionization requirements.

### Lane I — #579 focused test-layout continuation

#579 is a narrow behavior-neutral continuation of the stable suite taxonomy established by #568/#570. It moves a verified-safe Accounting/Usage integration cohort into `tests/integration/accounting/` with responsibility-based filenames while preserving path-bound conformance tests at their stable root locations.

Constraints:

- no production behavior changes;
- moved tests should remain behavior-identical where possible;
- exact-path references must be updated deliberately;
- path-sensitive/conformance-bound tests remain unmoved unless their consumers are explicitly migrated;
- full pytest discovery and protected-branch checks remain green;
- Platform Conformance remains green.

PR #580 currently implements this cohort. #579 is maintenance work, not a new operational-v1 feature dependency.

## What can run in parallel now

Maximum branch count is not the goal. The useful split is based on ownership, dependencies and collision zones.

| Track | Owner | Current state | Character | Primary collision zone |
|---|---|---|---|---|
| 1 | #439 | Active | Core convergence | planning, AgentRuntime, #384 handoff, Control Plane |
| 2 | #440 | Active | Evidence/performance | benchmarks, runtime/distributed fixtures |
| 3 | #501 | Active | Optional completion | governance, Approval, persistence, Search |
| 4 | #502 | Ready | Optional capability expansion | Repository, Capability/Plugin, Search |
| 5 | #388 | Ready when two-host environment exists | Real-host acceptance | deployment/network/security evidence |
| 6 | #560 | Implementation active, but merge blocked by current #439 hard dependency | Client semantics | coordination projection/Web/CLI |
| 7 | #46 | Evidence accumulation now; final close later | Final convergence | conformance/CI/release evidence |
| 8 | #566 | Ready, optional | Large HA productionization | Control Plane, persistence, deployment/security |
| 9 | #562 | Blocked by current #46 hard dependency | Real-host acceptance | deployment/network/security evidence |
| 10 | #579 | Active maintenance | Behavior-neutral test migration | Accounting test paths/conformance selectors |

## Recommended active concurrency

A practical split is:

1. **#439** — continue planning/replanning closure.
2. **#440** — continue non-destructive and deterministic benchmark evidence that does not depend on unfinished planner/HA profiles.
3. **#501** — finish optional governance recovery hardening.
4. **#560** — implementation may continue, but merge must wait for #439 closure or explicit dependency revision.
5. **#579** — small behavior-neutral test migration can proceed if it avoids active test-file collisions.
6. **#502** — can proceed in parallel because it is comparatively isolated from the active core branches.
7. **#388** — can proceed once the hardened two-host environment is available.
8. **#46** — continuously accumulate evidence without turning it into another broad implementation rewrite.
9. **#566** — may proceed as a dedicated optional architecture stream if capacity exists.

#562 remains blocked under its current dependency declaration.

## Collision map

### Low-collision / useful parallel pairs

- #439 with most #440 pressure/benchmark work;
- #501 with #440;
- #502 with #440;
- #502 with #388 operational acceptance;
- #566 with repository-only #502 work;
- #579 with branches that do not touch Accounting/Usage tests or exact conformance selectors.

### Higher-collision / sequence carefully

- #439 with #560: #560 consumes stable planner/coordinator semantics and currently hard-depends on #439.
- #439 with #440 planner profiles: stabilize planner loop first, then measure it.
- #566 with broad Control Plane/persistence/deployment changes: avoid simultaneous rewrites of the same composition/security seams.
- #46 with unfinished core owners: accumulate evidence continuously, but reserve final issue-wide closure for a stable candidate.
- #388 and #562 may share infrastructure, but #562's hard dependency still governs issue execution/merge order.
- #579 with any branch editing the same Accounting/Usage test cohort or path-bound conformance selectors.

## Recommended sequence from here

### Wave 1 — finish current non-blocked work

```text
#439  replanning/integration closure work
#440  independent benchmark evidence
#501  optional governance completion
#579  focused behavior-neutral test migration
#502  optional repository-intelligence work as capacity allows
#388  real-host transport acceptance when infrastructure is ready
```

#560 implementation can be prepared in parallel, but its current #439 dependency must be satisfied or explicitly revised before merge/closure.

### Wave 2 — close #439, then consume the stable planner contract

Once #439 is actually closed/stable:

```text
#560  merge/close if its implementation remains valid and green
#440  planner/replanning performance profiles
#46   Goal/Task -> generated Plan -> execution -> evidence -> bounded-replan conformance
#502  planner repository-intelligence context funnel, if justified
```

If #560's dependency is intentionally changed instead, update the issue first and re-evaluate the implementation against the revised contract before merge.

### Wave 3 — real-host distributed evidence

```text
private two-host topology
   -> #388 transport-specific acceptance
   -> sanitized transport evidence
      -> #440 real-network operating-envelope evidence where applicable

#46 closed OR #562 dependency explicitly revised
   -> #562 full distributed deployment/failure/security acceptance
   -> sanitized deployment evidence
```

Do not encode a circular close order as an undocumented exception.

### Wave 4 — final core convergence

After #439 and #560 are accepted and the required #440 evidence for the claimed baseline is available:

```text
exact release candidate
   -> required CI/security checks
   -> conformance matrix
   -> migration/backup/recovery evidence as claimed
   -> performance/operating-envelope evidence
   -> #46 final issue-wide audit
   -> operational v1 release candidate
```

#46 should remain the final core convergence owner rather than being closed merely because most component issues are done.

### Optional HA wave — #566

#566 can proceed independently of the ordinary baseline:

```text
real CoordinationProvider
   + HA-capable durable-state composition
   + active/passive deployment profile
   + real process failover/fencing/recovery
      -> #440 optional real-HA load profile
      -> #46 optional real-HA conformance
```

Do not make this a prerequisite for ordinary single-node release readiness unless a release explicitly claims real HA compatibility.

## Dependency/convergence picture

```text
                           current main
                                |
                 +--------------+--------------+
                 |              |              |
               #439           #440          #501/#502*
                 |              |              |
                 v              |           optional
               #560             |              |
                 |              |              |
                 +--------------+--------------+
                                |
                                v
                         #46 core convergence

#388 transport acceptance ---> real-host evidence

#46 closed OR dependency revised ---> #562 full VPS acceptance

#566* real Control Plane HA --------------------> #440/#46 optional real-HA evidence

#579 test-layout continuation ----> small behavior-neutral maintenance lane

#500, #567, #568 = completed convergence/maintenance owners

* optional ideal-end-state/profile work; not a hidden single-node baseline prerequisite.
```

## Progress interpretation

Use separate progress views rather than one misleading percentage.

### 1. Objective issue-count progress

**104 / 114 issues closed = about 91.2%.**

This is the only objective percentage. It is sensitive to issue creation/closure and says nothing about issue size. The addition of #579 reduces the percentage without reducing implemented capability.

### 2. Core operational-v1 maturity

Planning estimate: **roughly 94–96% implemented**, with release/conformance readiness somewhat lower because the remaining work is disproportionately integration- and evidence-heavy.

Why this is high:

- almost all foundational and product domains are implemented;
- durable workflow coordination is complete;
- distributed Worker/runtime foundations are present;
- #421 is closed;
- #500 pressure/admission runtime work is closed;
- #567 kernel mutation-boundary hardening is closed;
- #568 stable test-layout foundation is closed;
- #439 has both planning foundation and runtime-binding work merged plus active replanning-evidence work;
- #440 already has substantial benchmark coverage;
- remaining core work is concentrated in #439, #560, selected #440 evidence and final #46 convergence.

#579 is behavior-neutral maintenance and does not lower product-capability maturity.

This is a planning heuristic, not a release claim.

### 3. Expanded ideal-end-state maturity

Planning estimate: **roughly 86–90%** when optional Proposal/Specification completion, repository-intelligence provider ecosystem work, real two-VPS acceptance and production-shaped HA are counted as part of the target.

The range remains lower mainly because #566 is a large optional productionization lane, #502 still includes genuine provider-evaluation/packaging work, and real-host acceptance remains unfinished. #579 adds maintenance effort but no new end-user capability.

## Relative remaining effort

A rough remaining-effort ordering is:

```text
#566 optional real HA productionization      = very large, but optional
#439 final planning/replanning closure       = high-value core integration
#440 final operating-envelope evidence       = broad evidence workload
#562 real two-VPS deployment acceptance      = operational/infrastructure-heavy, currently blocked
#560 workflow semantic/live-refresh followup = focused medium block, currently dependent on #439
#502 optional provider ecosystem work        = medium, scope depends on candidate quality
#501 optional final governance closure       = likely small if its closure PR lands cleanly
#388 transport-specific two-host acceptance  = narrow if shared with the later #562 topology
#579 accounting test migration               = small behavior-neutral maintenance cohort
#46 final audit                              = convergence/evidence rather than greenfield code
```

This ordering estimates likely remaining work, not priority. Optional #566 may be the largest lane while still remaining outside the ordinary single-node release critical path.

## Release interpretation

A first operational release does **not** need every optional ideal-end-state feature or maintenance follow-up to be finished.

The release candidate should be judged by the exact supported profile:

- single-node baseline remains first-class;
- no recurring paid AI/API service is required;
- disabled optional Registry/intelligence/governance/HA profiles do not invalidate the baseline;
- real cross-host compatibility is claimed only when the corresponding #388/#562 evidence exists;
- real multi-Control-Plane HA compatibility is claimed only when #566 evidence exists;
- completed #500 pressure support is claimed only for profiles that actually enable and exercise it;
- completed #567/#568 hardening/maintenance should remain protected by regression and architecture checks;
- #579 remains behavior-neutral and should not silently become a product-release blocker beyond keeping required tests/conformance green;
- #46 records explicit evidence for every capability the release actually claims.

The project should therefore optimize for **clean convergence of the claimed baseline** while allowing optional #501/#502/#566 work, focused maintenance such as #579 and real-host validation to proceed without silently extending the release critical path.