# Dependency-Driven Implementation Roadmap

> Status baseline: 2026-09-07, post-closure audit refresh

This roadmap describes the remaining work from current `main` toward the operational v1 baseline and the wider ideal end state. The project is no longer in foundational platform construction. Most canonical domains, runtime boundaries, client surfaces, distributed execution building blocks and operational foundations are implemented; the remaining work is concentrated in convergence, architecture hardening, real-host acceptance, performance evidence and optional end-state extensions.

GitHub issue state, current issue comments and merged code remain the point-in-time source of truth. The normative product and architecture baseline remains:

- [`PRODUCT_VISION.md`](PRODUCT_VISION.md)
- [`ARCHITECTURE_PRINCIPLES.md`](ARCHITECTURE_PRINCIPLES.md)
- accepted ADRs under [`adr/`](adr/README.md)

Issue numbers are identifiers, not an implementation sequence. Explicit hard dependencies take precedence over this planning view whenever they differ.

## Current snapshot

There are **11 open issues out of 112 repository issues**:

`#46, #388, #439, #440, #500, #501, #502, #560, #562, #566, #567`

That is **101 closed / 112 total = about 90.2% closed by issue count**.

This percentage is bookkeeping only. It fell from the previous snapshot because post-closure audits added new follow-up issues; it does not imply that merged implementation was lost. Issue scope is highly uneven and several remaining issues are optional profile owners rather than baseline blockers.

There are **no open pull requests** at this snapshot, so `main` is at a useful synchronization point for choosing the next parallel work split.

## What is already materially implemented

The repository already contains, among other things:

- canonical Goal/Task/Plan/Step/Run/Event lifecycle and persistence;
- platform-owned kernel lifecycle authority;
- provider-neutral orchestration, execution, model, capability/tool, persistence and transport boundaries;
- reference execution plus optional Hermes, Forge and LiteLLM integration paths;
- versioned Control Plane, authentication, authorization/Approval and secret-reference boundaries;
- Agents and Agent Teams;
- Projects, Workspaces, Files, Artifacts, Memory and Knowledge;
- Search, Automations, Notifications and Verification/Review;
- Browser, Terminal, Chat, Web UI and CLI entry points;
- Organizations/Teams/Memberships and practical Task management;
- accounting/resource attribution;
- durable Connectors and Repository/Git integration;
- reusable workflow definitions, capability assignments and model-routing profiles;
- portable import/export, Templates and optional Registry/Marketplace;
- supported single-node deployment and optional HA semantics;
- canonical Node/Worker scheduling and authenticated distributed execution;
- network-capable MessageTransport implementation and remote Workspace materialization;
- durable Plan/Step coordination with dependencies, waits, retries, fan-out/fan-in, cancellation and reconciliation;
- Web/CLI workflow-progress views through the canonical Control Plane;
- platform-owned autonomous planning/replanning foundations and planned Step-to-Agent runtime binding;
- optional Proposal/Specification governance with exact-revision Approval binding and idempotent Task conversion;
- provider-neutral repository-intelligence baseline and production Repository-policy wiring;
- portable host-pressure admission, Linux pressure collection, observability, authenticated Worker reporting and deployment/doctor integration;
- release/update/upstream synchronization machinery;
- broad prototype/platform conformance frameworks;
- substantial single-node, coordination, API, distributed fault and heterogeneous-placement performance evidence.

## Changes since the previous snapshot

The previous 9-open/109-total snapshot became stale almost immediately after its documentation PR merged.

Important changes:

- #78 remains closed.
- #421 remains closed via PR #540; #560 is its narrower semantic/live-refresh follow-up.
- #439 gained the substantial runtime-completion work merged in PR #559 after the initial planning implementation in PR #546; the parent issue remains open until its current Definition of Done is actually proven.
- #500 now has the portable pressure core, Linux PSI/swap/zRAM/cgroup provider, observability integration, authenticated remote reporting and deployment/doctor integration merged.
- #440 gained further distributed Worker/Workspace fault and heterogeneous-placement benchmark coverage.
- #501 has a large merged governance implementation via PR #541.
- #502 has both its deterministic provider-neutral foundation and production Repository-policy wiring merged through PRs #539 and #556.
- #562 was added for production-shaped two-VPS/private-tunnel validation.
- #566 was added by a post-#89 audit for real multi-process/multi-host Control Plane HA productionization.
- #567 was added by a post-#88/#157 audit to remove canonical application dependence on the private kernel `_commit_task_command()` primitive.

## Remaining work by ownership lane

### Lane A — #439 autonomous planning/replanning closure

#439 remains one of the highest-weight core convergence owners.

Already merged:

- provider-neutral planning proposals/revisions;
- deterministic and model-backed planner paths;
- DAG/reference/satisfiability validation;
- durable planning state and restart behavior;
- Control Plane planning commands/projections;
- #384 activation/handoff;
- bounded-replanning foundations;
- exact planned Step-to-Agent runtime binding and planned execution context reaching AgentRuntime.

The issue must still close against its current audit, not against an older PR description. The intended completed loop is:

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

The final audit must prove any still-open evidence-to-replan, server-resolved policy-aware inventory, supersession/concurrency, evaluation, observability and required-test criteria. #439 must not absorb #384 coordination, #14 scheduling, #10 model routing, #12 capability ownership, #15 Approval or #86 Verification.

**Planning estimate:** implementation is advanced; treat this as final core integration/evidence work rather than a greenfield planner.

### Lane B — #567 supported canonical Task mutation boundary

#567 is new but focused. It is an architecture-hardening issue rather than a new product subsystem.

Current problem:

- `task_management` and `task_reassignment` have legitimate canonical operations that currently call private `PlatformKernel._commit_task_command()` internals.

Required outcome:

- expose the narrowest supported public Task mutation/command boundary;
- preserve terminal Task planning-metadata behavior from #88;
- preserve full #157 canonical Project reassignment semantics;
- preserve idempotency, revisions, causation/correlation, event ordering, provenance and audit;
- do not expose a generic arbitrary-event append escape hatch;
- add an architecture guard that prevents non-kernel modules from coupling to selected private kernel command primitives again.

This work is comparatively isolated in the kernel/task-management/task-reassignment area and is therefore a strong candidate for immediate parallel execution.

It should finish before the final #46 core conformance audit so the new encapsulation invariant can be included where useful.

### Lane C — #500 host-pressure/admission completion

#500 is a core operational-v1 hardening lane and is now highly advanced.

Merged implementation includes:

- OS/hardware-neutral pressure snapshots and normalized pressure states;
- deterministic pressure-aware admission integrated before reservation without creating a second scheduler;
- protected-headroom policy;
- optional Linux PSI/swap/paging/zRAM/cgroup/filesystem/descriptor evidence;
- #16 observability integration;
- authenticated and freshness-aware remote Worker pressure reporting;
- distributed deployment composition and `platform doctor` visibility.

Remaining work should be taken only from the current issue acceptance checklist and latest merged state. Likely remaining emphasis is final acceptance/operator guidance and the handoff to #440's dedicated pressure benchmark family rather than rebuilding the pressure architecture.

Invariants remain:

- #14 is the sole scheduler/reservation authority;
- Linux specifics remain provider metadata;
- `unknown` remains a valid portable state;
- swap/zRAM is never treated as equivalent physical RAM;
- collection is read-only by default;
- no automatic host tuning is introduced silently.

### Lane D — #440 performance/load/stress/scalability closure

#440 is a mature benchmark program, not a missing benchmark foundation.

Merged evidence includes:

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
- heterogeneous capability/resource/GPU/label/network placement benchmarks.

High-value remaining blocks are now mainly:

- real network/cross-host operating-envelope measurements where reproducible;
- release-sized comparable 1/10/50/100+ sweeps and longer endurance runs;
- measured operating envelopes and justified regression/noise budgets;
- persistence failure/contention only through stable provider seams;
- #500 pressure-under-load profiles once #500's contract is considered complete;
- #439 planner/replanning overhead profiles once #439 stabilizes;
- optional real HA load/failover profiles once #566 provides a production-shaped HA path.

Correctness, security, Verification and durability remain hard benchmark invariants.

### Lane E — #560 workflow-progress semantic/live-refresh completion

#421 is closed; #560 owns the deliberately narrower follow-up:

- safe canonical Approval/Event/external-job wait context;
- explicit active/resolved/expired/rejected/cancelled wait semantics;
- explicit retry-scheduled vs retry-exhausted semantics;
- stable attempt/budget evidence;
- reliable Web refresh for coordinator-only transitions;
- production-shaped Web and CLI parity;
- maintained #46 client-conformance evidence.

The architecture stays:

```text
Web / CLI
  -> versioned Control Plane
  -> canonical Plan/Step coordination projection
  -> #384 coordinator
```

No client shadow state or direct coordinator persistence access is allowed.

**Dependency status:** #560 currently lists #439 as a hard dependency. Preparatory analysis/tests may be possible, but under the repository's dependency discipline the full implementation lane should be considered ready after #439 closes or after that dependency is explicitly revised.

### Lane F — #46 final platform conformance

#46 is the core convergence owner and should be the last core issue to close for the operational profile.

The repository already has a strong continuous vertical including the Worker-produced Artifact -> Verification return path. #46 should continuously accumulate evidence, but final closure should wait for the exact claimed core profile.

Final audit should consume, where applicable:

- #439 full planning/replanning loop;
- #567 kernel encapsulation invariant;
- #560 workflow-client parity/live semantics;
- #500 pressure-aware evidence only if the claimed v1 profile enables it;
- #440 release/operating-envelope evidence;
- #388/#562 evidence only for releases claiming real cross-host distributed compatibility;
- #566 evidence only for releases claiming real multi-instance HA.

Optional features must report `supported`, `disabled`, `unsupported` or `not-run` explicitly rather than silently blocking the baseline or creating false compatibility claims.

### Lane G — shared real-host campaign: #388 + #562

These two issues should not be treated as two unrelated VPS projects.

#### #388 transport-specific acceptance

The transport implementation is present. Remaining acceptance requires:

- actual two-host encrypted/authenticated transport;
- dispatch and result retrieval across the real network boundary;
- restart/reconnect without changing canonical Worker identity;
- explicit proof that canonical Artifact/evidence references survive the return path.

#### #562 full two-VPS private-tunnel acceptance

#562 expands that infrastructure into a production-shaped distributed deployment:

```text
Host A: Control Plane / scheduler
        |
        | private authenticated tunnel
        v
Host B: remote Node / Worker
```

It adds:

- private tunnel/network exposure validation;
- authenticated registration and heartbeat;
- capability/resource-based remote dispatch;
- real result/reconciliation;
- deliberate tunnel interruption;
- liveness expiry and no new placement on unreachable Worker;
- tunnel restoration/re-registration;
- independent Worker restart;
- sanitized acceptance evidence.

**Recommended execution:** build one hardened two-VPS test topology and use it first for #388's narrow transport assertions, then for #562's broader deployment/failure/security matrix. Reuse the same sanitized evidence in #46/#440 where appropriate.

#562 currently has wording that lists #46 as a hard dependency while also requiring its result to feed #46 conformance evidence. That dependency should be interpreted/reconciled explicitly before normal issue execution rather than creating a circular close-order rule.

### Lane H — #501 optional Proposal/Specification governance

PR #541 already delivered a large portion of #501:

- versioned Proposal intake;
- immutable Specification revisions and stable digests;
- exact-revision Approval binding;
- stale-Approval invalidation semantics;
- restart-safe/idempotent Specification -> canonical Task conversion;
- direct Task creation remains supported;
- exact approved-revision planning input;
- Control Plane, Search, audit, Web and CLI surfaces;
- persistence/recovery coverage.

The remaining work should be identified through a fresh checklist audit, not by reimplementing the foundation. Any signal-to-Proposal automation, external specification adapter or deeper planner integration remains optional and must preserve the direct Task path.

### Lane I — #502 optional repository intelligence

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

### Lane J — #566 optional production-shaped Control Plane HA

#89 established the HA semantics and deterministic reference fixtures. #566 is the new productionization owner for a real independent-process/host profile.

It requires:

- at least one self-hostable `CoordinationProvider` implementation usable across independent processes/hosts;
- atomic acquire/renew/release and monotonic fencing epochs;
- backend-owned lease expiry and stale-authority rejection;
- health/readiness and fail-closed coordination outage behavior;
- a durable-state composition safe for active/passive promotion;
- shared/replicated canonical state rather than process-local objects or unsupported shared-file SQLite;
- real active/standby process replacement and failover acceptance;
- preservation of auth/session/revocation, event cursors, idempotency, Workers, Automations and reconciliation state;
- optional two-host acceptance;
- no paid hosted coordination requirement.

#566 is architecturally important for the full ideal end state but **optional for ordinary single-node operational v1**. It is also a relatively large new subsystem-integration lane and should not be allowed to destabilize core convergence by colliding broadly with Control Plane/persistence/deployment changes.

Once stable it can feed:

- optional HA fault-under-load profiles in #440;
- real HA conformance evidence in #46.

## What can run in parallel now

Technically many issues are open, but maximum branch count is not the goal. The safest useful split is based on ownership/collision zones.

| Track | Owner | Ready now? | Character | Primary collision zone |
|---|---|---:|---|---|
| 1 | #439 | Yes | Core convergence | planning, AgentRuntime, #384 handoff, Control Plane |
| 2 | #567 | Yes | Core architecture hardening | kernel, task management, task reassignment |
| 3 | #500 | Yes | Core operational hardening | scheduler, Node/Worker pressure, telemetry, deployment |
| 4 | #440 | Yes | Evidence/performance | benchmarks, runtime/distributed fixtures |
| 5 | #501 | Yes | Optional completion audit | governance, Approval, Search, Web/CLI |
| 6 | #502 | Yes | Optional capability expansion | Repository, Capability/Plugin, Search |
| 7 | #388 + #562 | Yes operationally, after safe two-VPS setup / dependency reconciliation | Real-host acceptance | deployment/network/security evidence |
| 8 | #46 | Yes as evidence accumulation; final close later | Final convergence | conformance/CI/release evidence |
| 9 | #560 | Full lane waits on current #439 dependency | Client semantics | coordination projection/Web/CLI |
| 10 | #566 | Yes, optional | Large HA productionization | Control Plane, persistence, deployment/security |

## Recommended active concurrency

Do **not** run all ten lanes as heavy code branches simultaneously.

A practical current split is **five to six focused workstreams**:

1. **#439 final planning/replanning closure**.
2. **#567 Task-mutation boundary hardening**.
3. **#500 final host-pressure acceptance/integration**.
4. **#440 remaining non-blocked benchmark evidence**.
5. **one optional feature lane: #502 or #501**, selected by highest current value.
6. **one operational lane: combined #388/#562 two-VPS campaign**, when the secure environment is prepared.

#46 can accumulate conformance evidence alongside these without necessarily becoming a separate broad implementation branch.

#566 can also start in parallel, but because it touches Control Plane/persistence/deployment architecture broadly, it is better treated as a dedicated optional architecture stream rather than mixed into every current branch. If engineering capacity is limited, defer heavy #566 implementation until the core convergence branches are quieter.

## Collision map

### Low collision / good parallel pairs

- #567 with #502;
- #567 with most #440 benchmark work;
- #439 with #500 when ownership boundaries are respected;
- #501 with #440;
- #388/#562 operational acceptance with repository-only #502 work.

### Higher collision / sequence carefully

- #439 with #560: #560 consumes stable planning/coordinator semantics and currently hard-depends on #439.
- #500 with #440 pressure profiles: implement/stabilize the pressure contract first, then benchmark it.
- #439 with #440 planner profiles: stabilize planner loop first, then measure it.
- #566 with broad Control Plane/deployment changes: avoid simultaneous rewrites of the same composition/persistence/security seams.
- #46 with all unfinished core owners: accumulate evidence continuously, but reserve final issue-wide audit for a stable candidate.

## Recommended sequence from here

### Wave 1 — close focused core architecture gaps

Run in parallel:

```text
#439  planning/replanning final loop
#567  public Task mutation boundary
#500  pressure/admission finalization
#440  independent remaining benchmarks
#501/#502 selected optional work
```

At the same time, prepare the secure two-VPS acceptance topology without exposing internal Worker services publicly.

### Wave 2 — fan out after #439

Once #439 is closed/stable, several consumers become safely parallel:

```text
#560  workflow wait/retry/live-refresh completion
#440  planner/replanning performance profiles
#46   Goal/Task -> generated Plan -> execution -> evidence -> bounded-replan conformance
#501  deeper approved-Specification -> planner integration, if still needed
#502  planner repository-intelligence context funnel, if justified
```

These should run concurrently rather than serially because they consume the same stable planner contract in different ownership domains.

### Wave 3 — fan out after #500

Once #500 is closed/stable:

```text
#440  PSI/swap/zRAM/cgroup/admission pressure profiles
#502  heavy indexing/rebuild pressure-aware admission evidence
#46   pressure-aware profile evidence only if the release claims that profile
```

Again, these are parallel consumers of the stable pressure contract.

### Wave 4 — real-host distributed acceptance

Use the same secure private topology:

```text
private two-VPS topology
   -> #388 transport-specific acceptance
   -> #562 full distributed deployment/failure/security acceptance
   -> sanitized evidence
      -> #46 optional distributed conformance
      -> #440 real-network operating-envelope evidence
```

This avoids maintaining two separate real-host test environments.

### Wave 5 — final core convergence

After #439, #567, #500 and #560 are accepted and the required #440 evidence for the claimed baseline is available:

```text
exact release candidate
   -> required CI/security checks
   -> conformance matrix
   -> migration/backup/recovery evidence as claimed
   -> performance/operating-envelope evidence
   -> #46 final issue-wide audit
   -> operational v1 release candidate
```

#46 should be the last core convergence issue, not the first umbrella issue closed.

### Optional HA wave — #566

#566 can proceed independently of the baseline:

```text
real CoordinationProvider
   + HA-capable durable-state composition
   + active/passive deployment profile
   + real process failover/fencing/recovery
      -> #440 optional HA load profile
      -> #46 optional real-HA conformance
```

Do not make this a prerequisite for ordinary single-node release readiness unless a release explicitly claims real HA compatibility.

## Dependency/convergence picture

```text
                              current main
                                   |
         +------------+------------+------------+------------+
         |            |            |            |            |
       #439         #567         #500         #440       #501/#502*
         |            |            |            |            |
         v            |            +-------> #440             |
       #560           |              pressure profiles        |
         |            |                                    optional
         +------------+------------------+---------------------+
                                      |
                                      v
                               #46 core convergence

#388 transport acceptance ----+
                              +--> shared real two-VPS campaign --> optional distributed evidence
#562 full VPS acceptance ------+

#566* real Control Plane HA --------------------> #440/#46 optional HA evidence

* optional ideal-end-state/profile work; not a hidden single-node baseline prerequisite.
```

## Progress interpretation

Use three different progress views rather than one misleading percentage.

### 1. Objective issue-count progress

**101 / 112 issues closed = about 90.2%.**

This is the only objective percentage. It is sensitive to new audit follow-ups and says nothing about issue size.

### 2. Core operational-v1 maturity

Planning estimate: **roughly 92–95% implemented**, with release/conformance readiness somewhat lower because the remaining work is disproportionately acceptance-heavy.

Why this is high:

- almost all foundational and product domains are implemented;
- durable workflow coordination is complete;
- distributed Worker/runtime foundations are present;
- #421 is closed;
- #439 has both planning foundation and runtime-binding work merged;
- #500 has nearly its entire architecture stack merged;
- #440 already has substantial benchmark coverage;
- remaining core work is concentrated in #439, #567, #500, #560, selected #440 evidence and final #46 convergence.

This is a planning heuristic, not a release claim.

### 3. Expanded ideal-end-state maturity

Planning estimate: **roughly 84–89%** when optional Proposal/Specification completion, repository-intelligence provider ecosystem work, real two-VPS acceptance and the newly added production-shaped HA profile are counted as part of the target.

The range is lower mainly because #566 is a large new optional productionization lane and #502 still includes genuine provider-evaluation/packaging work.

## Relative remaining effort

A rough remaining-effort ordering is:

```text
#566 optional real HA productionization      = very large, but optional
#439 final planning/replanning closure       = high-value core integration
#440 final operating-envelope evidence       = broad evidence workload
#562 real two-VPS deployment acceptance      = operational/infrastructure-heavy
#500 final pressure closure                  = smaller than before; architecture largely merged
#560 workflow semantic/live-refresh followup = focused medium block after #439
#502 optional provider ecosystem work        = medium, scope depends on candidate quality
#501 optional final governance audit         = likely relatively small after PR #541
#567 kernel mutation-boundary hardening      = focused bounded core cleanup
#388 transport-specific two-host acceptance  = narrow if shared with #562
#46 final audit                              = convergence/evidence rather than greenfield code
```

This ordering is about likely remaining work, not issue priority. A smaller core architecture issue such as #567 can be more urgent than a larger optional issue such as #566.

## Release interpretation

A first operational release does **not** need every optional ideal-end-state feature to be finished.

The release candidate should be judged by the exact supported profile:

- single-node baseline remains first-class;
- no recurring paid AI/API service is required;
- disabled optional Registry/intelligence/governance/HA profiles do not invalidate the baseline;
- real cross-host compatibility is claimed only when #388/#562 evidence exists;
- real multi-Control-Plane HA compatibility is claimed only when #566 evidence exists;
- #46 records explicit evidence for every capability the release actually claims.

The project should therefore optimize for **clean convergence of the claimed baseline**, while allowing optional #501/#502/#566 work and real-host validation to proceed in parallel without silently extending the release critical path.
