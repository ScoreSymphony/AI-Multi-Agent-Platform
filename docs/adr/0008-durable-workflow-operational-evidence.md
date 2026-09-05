# ADR 0008 operational evidence addendum

- **Parent decision:** ADR 0008 — Defer an external durable workflow engine and add only missing platform durability
- **Issue:** #21
- **Implementation follow-up:** #384
- **Evidence reviewed:** 2026-09-05
- **Post-close hardening:** PR #405
- **Decision impact:** None. ADR 0008 remains Accepted with Outcome 2.

## Purpose

This addendum closes the remaining documentation-detail gaps found during the completion audit of #21. It does not reopen the architecture decision and does not introduce Temporal, DBOS or another workflow engine.

ADR 0008 already establishes that an external engine is not justified for the current gap. This note makes the self-hosted Temporal operational footprint more explicit in the three areas that #21 asked to evaluate directly:

- persistence/database topology;
- networking, TLS, authentication, authorization and observability;
- CPU/RAM sizing evidence and why no universal minimum should be treated as a platform requirement.

The resulting conclusion remains unchanged: the narrow platform-owned Plan/Step coordinator from #384 is the smaller baseline for the current platform, while Temporal remains a future adapter candidate when the documented re-evaluation triggers are reached.

## 1. Persistence and database topology

Temporal's self-hosted service is not stateless coordination logic. Temporal documents a service composed of Frontend, History, Matching and Worker services, with durable workflow state written to persistence so executions can survive process or machine failure.

For a production self-hosted deployment, static configuration includes at least:

- Temporal service topology;
- service membership ports;
- persistence configuration, including History shard count;
- Visibility storage/configuration;
- optional Archival configuration;
- TLS/authentication/authorization configuration;
- metrics/logging configuration.

This means a production Temporal adoption would add an independently versioned persistence/schema lifecycle to the platform even if canonical Task/Plan/Step/Run/Event state remains platform-owned.

### Consequence for this platform

If Temporal were introduced behind the future durable-coordinator adapter, the platform would have to operate and recover two durable state domains:

1. platform canonical persistence; and
2. Temporal workflow execution/history persistence.

Temporal history would remain non-canonical by ADR 0008, but it would still be operationally required for active Temporal-backed workflows. Backup, restore and disaster recovery therefore cannot treat it as disposable cache state.

A future implementation must define cross-store recovery invariants before enabling the adapter in production. At minimum:

- restore order must be documented;
- restored engine state must be reconciled against canonical Plan/Step/Run state;
- adapter reconciliation must never create replacement canonical Tasks/Runs merely because Temporal state is missing or temporarily unavailable;
- incompatible or ambiguous restored state must surface a repair disposition instead of silently advancing lifecycle state.

This is materially more operational surface than #384's selected reference coordinator, which can join the existing platform-owned persistence/backup contract.

## 2. Networking and security surface

Temporal's self-hosted configuration explicitly includes security configuration. Current Temporal documentation describes mTLS for two distinct traffic classes:

- **internode** traffic between Temporal Service nodes; and
- **frontend** traffic between application clients/workers and the Frontend Service.

Self-hosted deployments can require client certificates and configure trusted CAs. Temporal also exposes pluggable authentication/authorization interfaces (`ClaimMapper` and `Authorizer`) that the Frontend Service invokes before API operations.

### Consequence for this platform

A production Temporal adapter would therefore add security and networking responsibilities beyond the platform's existing Control Plane/Worker boundaries:

- certificate issuance, rotation and trust for Temporal internode and frontend traffic where mTLS is enabled;
- additional network endpoints and firewall/routing rules;
- service-to-service identity decisions;
- authentication/authorization plugin configuration if API access must be restricted beyond transport identity;
- another dependency whose security configuration and upgrades must be tracked independently from platform authorization policy.

These mechanisms are useful and production-capable. They are nevertheless additional operational work; they do not replace the platform's own #15 authorization/Approval semantics or canonical scope enforcement.

## 3. Observability and operation

Temporal documents metrics for self-hosted services and supports Prometheus-compatible metrics output. Production guidance also covers monitoring, Visibility, namespaces, upgrades and schema upgrades separately from application code.

A Temporal-backed deployment would therefore introduce another diagnostic plane alongside platform telemetry:

- Temporal service health and request/error rates;
- History/Matching/Frontend/Worker service saturation;
- persistence latency and errors;
- workflow/task-queue metrics;
- Temporal-specific upgrade/schema health;
- platform-side adapter/reconciliation telemetry.

The platform must keep canonical Event/telemetry evidence authoritative for externally visible lifecycle explanation. Temporal metrics/history may enrich diagnostics but may not become the only way to determine why a canonical Task/Step/Run is in a given state.

## 4. CPU/RAM footprint: what can and cannot be concluded

### No universal production minimum

Temporal's official self-hosted documentation does not provide one universal CPU/RAM minimum that would be valid for this platform's workloads. That is expected: resource use depends on workflow state-transition throughput, persistence performance, History load, task queues, schedules, SDK worker caches and deployment topology.

ADR 0008 was therefore correct not to invent a precise platform requirement without benchmarking.

### Illustrative current sizing evidence

A February 2026 Temporal Community Forum response from a Temporal team member gives workload-dependent starting points for roughly medium/large throughput examples (described there as approximately 30/60 state transitions per second):

| Temporal service | Illustrative starting point from the forum response |
| --- | ---: |
| Frontend | 1.5–2 CPU cores, 4 GiB memory |
| History | 4 CPU cores, 6+ GiB memory |
| Matching | 1 CPU core, 2 GiB memory |
| Worker Service | 0.5–1 CPU core, 1 GiB memory |

The same response explicitly says actual requirements depend strongly on workload and persistence throughput and that smaller workloads may need substantially less. It also points to state-transition and persistence-request metrics, History shard configuration and cache tuning as important sizing variables.

These values are **not** adopted as platform requirements and are **not** an official universal Temporal minimum. They are retained only as evidence that a production Temporal service can have a material baseline footprint once separated into production services, and that sizing must be measured rather than guessed.

### Decision rule for this project

Before any future Temporal adoption, the re-evaluation must benchmark the candidate against the reference coordinator using representative workloads. Record at least:

- idle CPU/RAM by service;
- CPU/RAM under representative short-task and long-wait workloads;
- state transitions per second;
- persistence request rate/latency;
- fan-out/fan-in throughput and tail latency;
- restart/recovery time;
- storage growth/history retention;
- operational repair behavior under persistence or service failure.

No resource number from generic guidance may become a platform capacity requirement without that benchmark.

## 5. Single-node versus distributed fit

### Single-node/self-hosted baseline

Temporal provides a convenient development server (`temporal server start-dev`) as a single binary without external dependencies, but Temporal's own documentation positions that path for local development/testing rather than as the production topology evaluated by #21.

For the platform's production-shaped single-node baseline, the relevant comparison is therefore:

- **#384 reference coordinator:** joins existing platform process/persistence/backup conventions and owns only missing Plan/Step coordination state;
- **Temporal production service:** adds a distinct service plane, persistence/Visibility configuration, security endpoints, monitoring and upgrade/schema responsibilities.

For the current narrow gap, the incremental operational cost remains disproportionate.

### Distributed profile

Temporal becomes more attractive when the platform requires stronger distributed durable-workflow semantics than the reference coordinator should reasonably implement, especially active-active/cross-region coordination, replay/version migration, very large workflow graphs or repeated operational recovery problems.

Those conditions are already explicit re-evaluation triggers in ADR 0008. This addendum does not change them.

## 6. Acceptance-criterion closure for #21

After this addendum, the operational/resource part of #21 is considered fully documented at the architecture-spike level:

- production service components/topology: documented;
- database/persistence implications: documented;
- CPU/RAM footprint: evaluated qualitatively, with workload-dependent current sizing evidence and an explicit benchmark rule;
- backup/restore implications: documented;
- upgrades/schema lifecycle: documented;
- observability: documented;
- networking: documented;
- TLS/authentication/authorization: documented;
- single-node suitability: documented;
- multi-node/distributed suitability: documented.

No Temporal prototype is required to close #21 because the decision is to defer adoption; a prototype becomes required only when a re-evaluation trigger is reached and implementations must be compared against the same canonical coordinator contract.

## 7. Source classification

### Official Temporal documentation

- Self-hosted guide: <https://github.com/temporalio/documentation/blob/main/docs/production-deployment/self-hosted-guide/index.mdx>
- Temporal Service configuration: <https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/temporal-service/temporal-service-configuration.mdx>
- Temporal architecture/how it works: <https://github.com/temporalio/documentation/blob/main/docs/encyclopedia/architecture/how-temporal-works.mdx>

These sources support the service topology, persistence, security, mTLS, authentication/authorization, metrics and development-vs-production statements above.

### Illustrative community sizing evidence

- Temporal Community Forum, "Baseline server resource consumption" (2026-02): <https://community.temporal.io/t/baseline-server-resource-consumption/19267>

This source is used only for illustrative workload-dependent sizing evidence. It is not treated as a normative product requirement or universal production minimum.

## Final conclusion

The additional operational evidence strengthens rather than changes ADR 0008:

- Temporal is technically capable and remains a valid future adapter candidate.
- The production self-hosted footprint is a meaningful additional service, persistence, security, monitoring and upgrade domain.
- No universal CPU/RAM requirement can be responsibly assigned without benchmarking the platform's workload.
- The current missing capability remains narrow durable Plan/Step coordination and belongs in #384.
- **Outcome 2 remains the accepted decision.**
