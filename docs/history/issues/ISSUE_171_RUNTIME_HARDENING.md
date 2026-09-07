# Issue #171 runtime hardening

Issue #171 follows the completed #76 accounting foundation and verifies the remaining cross-domain integrations against the real source runtimes. #76 remains the sole canonical accounting authority: Workspaces, Node/Worker runtime, Agent/Team runtime, Organizations/Memberships and Notifications contribute canonical facts without introducing a second accounting store, budget model or usage identity.

## Completed integration evidence

The completed hardening and re-audit coverage now proves:

- completed #14 `DistributedRuntime` heartbeat/resource facts flow through #16 into #76 with canonical `node_id` / `worker_id` attribution;
- missing/default #14 hardware values are not fabricated as zero accounting measurements, while explicitly unavailable values preserve `MeasurementQuality.UNAVAILABLE` and `quantity=None`;
- a reliably reported real numeric zero remains distinguishable from an unavailable measurement;
- provider/transport replacement does not redefine canonical Node/Worker accounting identity;
- opaque provider-native resource measurements remain namespaced `*.provider_reported.*` values instead of being falsely normalized;
- distinct Node/Worker `latest` gauges remain scope-safe across multiple canonical scopes;
- completed #33 `AgentRuntime` / `AgentRunRecord` identity and revision provenance survive later Agent/Team definition edits and orchestrator replacement;
- Agent/Team trace identity survives the remote Worker transport path and reaches #76 attribution;
- real #87 Organization aggregate visibility and Team-scoped grants do not widen into unrelated Organization/Team scopes;
- Team aggregate access is jointly constrained by the real #15 authorization gate and real #87 membership state: authorization alone does not reveal Team accounting, and membership never widens a #15 denial;
- aggregate projections remove person-level Task/Run/Agent identifiers while raw `usage-records` remain exact-owner isolated;
- Workspace logical snapshot accounting remains separate from physical FileProvider storage, archives retain the latest logical footprint, and canonical deletion retires current gauges;
- canonical #75 budget-threshold Notifications preserve preferences, recipient isolation, minimized accounting summaries, threshold-generation dedupe and restart recovery;
- dismissed and archived threshold attention are not resurrected after restart, while a legitimate later threshold re-cross can create a new attention episode;
- canonical accounting collections (`usage-records`, `usage-aggregates`, `usage-budgets`) cannot be silently replaced by later extension registration.

## Control Plane and deployment composition

When an `AccountingService` is configured on the supported single-node deployment, the same canonical service instance is now passed into the effective `ControlPlane`. The deployment therefore exposes the canonical `usage-records`, `usage-aggregates` and `usage-budgets` collections and reuses that same #76 authority for #75 threshold-attention composition.

Accounting remains optional for deployment profiles that do not configure it; #171 does not make Accounting mandatory. Organization-aware accounting likewise remains an optional composition when an `OrganizationService` is supplied. The Organization layer replaces only the read projection and does not create another accounting authority.

The Control Plane consumes both accounting resource factories through the shared read-only `Mapping[str, ResourceService]` boundary, keeping base and Organization-aware projections substitutable without changing the canonical #76 persistence/service model.

## Dependency status

The historical #171 dependency text is superseded by the current repository state:

- #37 Workspace/project environment management — completed;
- #14 Node/Worker runtime/resource contracts — completed;
- #33 Agent/Team runtime and executed identity contracts — completed;
- #75 Notifications/user-attention system — completed and no longer gating #171;
- #87 Organization/Team/Membership management — completed and no longer gating #171;
- #44 Connector framework — completed, optional progressive measurement source;
- #74 Browser capability — completed, optional progressive measurement source;
- #82 Repository/Git — optional progressive measurement source and not a #171 blocker unless a trustworthy measurement integration is explicitly scoped later.

Optional Connector/Browser/Repository measurements must continue to use existing #76 seams only when their owning domains expose reliable semantics. Missing network bytes, external cost, hardware usage or other measurements remain unavailable rather than inferred.

## Architectural invariants retained

- #76 remains the canonical provider-neutral accounting authority.
- #14 owns Node/Worker resource and scheduling facts.
- #33 owns Agent/Team definition and execution identity.
- #37 owns Workspace/Snapshot lifecycle and storage semantics.
- #87 owns Organization/Team/Membership relationships.
- #75 owns Notification delivery and user-attention state.
- #15 remains authoritative for access/admission decisions.
- #16 remains observability/measurement plumbing, not durable accounting truth.
- Provider-, transport-, orchestrator- and deployment-specific identifiers remain metadata rather than canonical scope identity.
- Missing or unreliable measurements are never converted into fabricated zero usage.

With the post-#399 hardening, PR #460 residual fixes and the final explicit #15 + #87 Team-access regression, the original #171 Definition of Done and Required-Test evidence are satisfied without reopening or redesigning #76.
