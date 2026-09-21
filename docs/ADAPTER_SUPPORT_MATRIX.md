# Adapter support matrix


The machine-readable source of truth is [`ADAPTER_SUPPORT_MATRIX.toml`](ADAPTER_SUPPORT_MATRIX.toml). This document defines how those entries are interpreted and records the consolidation decisions behind them.

## Why this exists

A platform-owned interface and a concrete implementation are different compatibility promises. The repository can keep a stable `Executor`, `ModelProvider`, `CapabilityToolProvider`, `BrowserProvider`, `RepositoryProvider`, or another replaceable boundary without promising that every implementation ever evaluated is maintained as a production option.

The matrix answers three separate questions:

1. Does a stable platform boundary exist?
2. Does a first-party implementation exist?
3. What support claim, if any, does the project make for that implementation?

Removing or demoting an adapter must never make a surviving vendor/runtime canonical.

## Support tiers

| Tier | Meaning | Compatibility expectation |
| --- | --- | --- |
| `reference` | Platform-owned deterministic/local baseline used to prove a contract and keep the reference product functional. | Must have maintained deterministic evidence and must not require a recurring paid service. |
| `supported` | Maintained optional implementation with a real product use/profile and an identified contract, integration, or platform-conformance path. | The compatibility claim is valid only for the concrete profile/version whose evidence passes. |
| `experimental` | First-party implementation kept for evaluation, prerelease protocol work, or evidence gathering. | Opt-in. Existence and unit/contract tests do **not** constitute a production compatibility claim. |
| `deprecated` | First-party implementation scheduled for removal or consolidation. | Must publish a migration path and removal criterion/target before deletion. |
| `external` | Public-contract implementation maintained outside core. | Core preserves the public contract under its versioning policy but does not own implementation compatibility. |

Support tier is a **product/maintenance policy**, not provider runtime state. It therefore does not belong in `ProviderDescriptor`; that contract remains backend-neutral technical metadata.

## Audited scope

This support audit covers product-facing replaceable implementation boundaries:

- planner and orchestrator;
- executor;
- model provider and model router;
- capability/tool provider and MCP client transport;
- browser provider;
- File/Memory/Knowledge providers;
- durable/reference persistence repositories and the supported single-node persistence topology;
- message transport;
- connector and repository providers;
- observability exporters;
- verification and evaluation providers;
- security-evidence providers;
- deployment-specific lifecycle bridges that expose an independently supported topology.

Persistence needs one important distinction. `src/ai_multi_agent_platform/backup/inventory.py` is the authoritative inventory of the supported single-node physical durable-store topology. Domain-owned SQLite/JSON stores are **not** competing implementations of one giant repository contract merely because they all persist data. The matrix therefore records the supported topology as `persistence.single-node-topology` and records concrete interchangeable repository pairs where they exist, including kernel and Agent repositories. The detailed physical inventory remains owned by `docs/runtime/PERSISTENCE_TOPOLOGY.md` and the backup inventory rather than being duplicated here.

The matrix does **not** create separate compatibility claims for helpers that merely decorate, transport, hydrate, or compose a listed provider. Examples include HTTP transport classes, observability wrappers such as `ObservedExecutor`, capability bridges, most deployment composition roots, and test fakes. Those helpers inherit the support status of the product-facing implementation they serve unless they expose an independently selectable compatibility profile. MCP clients are listed separately because protocol-revision/transport compatibility is independently claimed and tested. `DistributedLifecycleBackend` is listed because it is the supported bridge that selects the advanced distributed Worker topology.

## Current supported surface

The complete entry-by-entry inventory, owner, burden, security surface, upstream state, usage and evidence mapping is in the TOML file.

| Boundary | Reference baseline | Supported optional | Experimental / deprecated / non-claiming |
| --- | --- | --- | --- |
| Planner | `planner.reference` | `planner.model-backed` | — |
| Orchestrator | `orchestrator.reference` | `orchestrator.hermes` | — |
| Executor | `executor.reference` | — | `executor.agent-sandbox`, `executor.openshell`, `executor.swe-rex` (experimental) |
| Model provider | `model.openai-compatible` | `model.litellm` | dedicated Bifrost/SGLang adapters intentionally absent |
| Model router | `model.router` | — | — |
| Capability/tool | `capability.native` | `capability.mcp` | — |
| MCP client transport | — | `mcp.python-sdk` | `mcp.stateless-http` |
| Browser | `browser.stdlib` | — | — |
| File | `data.file.local` | — | — |
| Memory | `data.memory.local` | — | — |
| Knowledge | `data.knowledge.local` | — | — |
| Single-node persistence topology | `persistence.single-node-topology` | — | — |
| Kernel repository | `persistence.kernel.in-memory`, `persistence.kernel.sqlite` | — | — |
| Agent repository | `persistence.agent.in-memory`, `persistence.agent.json` | — | — |
| Message transport | `messaging.in-process` | `messaging.tcp` | — |
| Connector | `connector.reference` | `connector.github-releases` | — |
| Repository/Git | `repository.local-git` | `repository.connector` | — |
| Observability exporter | `observability.noop`, `observability.in-memory` | `observability.accounting-bridge` | — |
| Verification | `verification.completion` | — | — |
| Evaluation | `evaluation.deterministic-assertions`, `evaluation.metric-threshold` | — | — |
| Security evidence | — | `security-evidence.skillspector` | — |
| Deployment lifecycle | — | `deployment.distributed-lifecycle` | — |

`executor.forge` was removed after its explicit reference-golden-path and backend-neutral conformance removal gates passed. That removal does not promote an experimental executor or change the canonical `Executor` contract. The guarantee-preservation audit and retained historical provenance are recorded in `docs/integrations/FORGE_RETENTION_DECISION.md` and ADR 0013.

## Consolidation decisions

### 1. One OpenAI-compatible HTTP model path, not one adapter per gateway/runtime

`OpenAICompatibleModelProvider` is the broad local/self-hosted reference path. LiteLLM deliberately reuses that transport stack and remains a supported optional adapter only for LiteLLM-specific configuration/identity semantics.

Bifrost and SGLang do not justify dedicated first-party `ModelProvider` implementations while their supported behavior is expressible through the OpenAI-compatible boundary. A dedicated adapter may be added only when a reviewed, supported capability cannot be represented cleanly through that standard boundary.

### 2. Forge retirement completed

The former `ForgeExecutor` compatibility surface and its `ForgeClient`/HTTP sidecar transport were removed after the reference golden path and backend-neutral conformance gates proved the required generic guarantees outside Forge-specific code.

`ReferenceExecutor` remains the baseline migration target. The removal deleted the executable adapter/transport, Forge-only tests, sidecar CI and current support/release claims together while preserving historical audit/provenance records. Reintroducing Forge would require a new adoption/support decision and fresh evidence; the old pin does not constitute a current compatibility claim.

### 3. MCP has one capability adapter and profile-specific clients

`MCPToolProvider` is the capability-side adapter. `MCPPythonSDKClient` is the supported stable MCP client profile. `MCPStatelessHTTPClient` is deliberately `experimental` while its upstream protocol/conformance line remains prerelease/informational.

The codebase therefore does **not** support “three MCP tool providers.” It supports one MCP capability projection with one stable claimed client profile and one prerelease experimental client profile.

### 4. In-process and TCP messaging are both justified

`InProcessMessageTransport` and `TcpMessageTransport` share the same contract but are not redundant: one proves deterministic same-process behavior; the other provides the self-hosted cross-process topology used by distributed Workers. Removing either would eliminate a distinct deployment shape.

### 5. The physical persistence inventory is one supported topology, not 38 fake provider alternatives

The single-node reference deployment currently owns multiple domain-separated SQLite and JSON stores. That physical inventory is a deployment/recovery contract, not evidence that every store is an alternative implementation of one repository interface. `persistence.single-node-topology` points at the authoritative durable-store inventory, while actual interchangeable repository implementations such as `InMemoryKernelRepository`/`SqliteKernelRepository` and `InMemoryAgentRepository`/`JsonAgentRepository` are classified separately.

This preserves the architectural rule that logical repository ownership is independent from physical storage topology.

### 6. NoOp and in-memory observability are both reference modes

`NoOpExporter` proves telemetry backends are optional and cannot become lifecycle authority. `InMemoryExporter` provides deterministic retained telemetry/timeline evidence. `AccountingBridgeExporter` is a semantic handoff to accounting, not a third general monitoring backend.

### 7. Local Git and connector-backed repositories are complementary

`LocalGitRepositoryProvider` executes local Git operations. `ConnectorRepositoryProvider` maps an already-supported Connector into repository semantics for hosted/self-hosted collaboration. The latter is a bridge over the connector boundary, not a second canonical repository model.

### 8. External executor experiments do not inherit production support from contract tests

`AgentSandboxExecutor`, `OpenShellExecutor` and `SwerexExecutor` implement the canonical Executor and have deterministic tests, but that does not make them supported. Agent-Sandbox remains evidence-gated by live isolation evaluation; OpenShell is explicitly `experimental_only` at the pinned alpha revision until one concrete runtime profile has retained isolation/egress/credential/resource evidence; SWE-ReX is explicitly evaluation/experimental-only. None belongs in an unqualified “supported executors” claim, and Forge removal does not change those tiers.

### 9. SkillSpector is supported only in the evaluated static evidence profile

The completed SkillSpector production integration provides a pinned, isolated `static_no_llm_network_none` SecurityEvidence provider. That makes `security-evidence.skillspector` `supported`, not merely experimental.

The support claim remains deliberately narrow. SkillSpector is advisory evidence only: it does not become Skill trust, Approval, install, enable, or activation authority, and the reference platform does not require it.

### 10. Deployment composition is normally inherited; the distributed lifecycle bridge is explicit

Most app/composition-root helpers inherit the support tier of the providers they compose. `DistributedLifecycleBackend` is classified explicitly because it is the lifecycle bridge that enables the independently supported distributed Worker topology and has its own distributed conformance/failure surface.

### 11. Helper variants inherit the parent support claim

The following classes/patterns do not get independent product tiers unless they later expose independently selectable compatibility semantics:

- Hermes HTTP transport helpers;
- `DurableGitHubReleaseConnectorProvider`, which hydrates the supported GitHub Releases connector from canonical persistence;
- `PlanningOrchestratorAdapter`, which bridges canonical activated plans into the Orchestrator seam;
- `ExecutorLifecycleBackend` and lower-level distributed transport/lifecycle decorators beneath the listed distributed profile;
- `BoundBrowserProvider`;
- connector/repository capability bridges;
- `Observed*` instrumentation decorators;
- ordinary deployment app/composition-root modules;
- testing fakes and benchmark-only fault providers.

Treating these as separate providers would inflate the support matrix without creating a distinct user-selectable implementation promise.

## Compatibility and conformance rule

An implementation can be `supported` only when the matrix names an evidence path. For external/upstream-backed implementations, the claim is scoped to the tested profile/revision; “adapter exists” is not enough.

The platform-wide conformance layer remains the aggregator for end-to-end compatibility claims. Focused contract/integration tests remain owned by their subsystem. This support matrix does not duplicate those suites: it maps support claims to them and fails closed when a supported entry lacks evidence.

In particular:

- the reference baseline remains deterministic, local-first and free from mandatory paid services;
- Hermes support requires its prepared external profile; Forge has no current compatibility profile after its executable retirement;
- LiteLLM requires its dedicated compatibility lane;
- stable MCP claims require protocol and platform evidence for the same profile;
- SkillSpector support is limited to the pinned static/no-LLM/network-none evidence profile;
- the distributed lifecycle claim requires the distributed platform profile rather than only an isolated class test;
- experimental implementations may have tests without becoming a compatibility claim.

## Deprecation/removal procedure

When a future audit moves an entry to `deprecated`, the same change must add:

1. the surviving target implementation or contract-level alternative;
2. migration guidance for configuration and persisted provider references if any;
3. the exact compatibility/CI lane that stops being maintained;
4. an explicit removal criterion or target release/date;
5. proof that canonical IDs/data do not depend on provider-private state;
6. a contract test demonstrating that an external/community replacement remains possible where the public boundary stays supported.

Only after those conditions are satisfied may the implementation and obsolete CI lane be removed.

For `executor.forge`, `docs/integrations/FORGE_RETENTION_DECISION.md` records the completed application of this procedure: deprecation first, explicit removal gates, then coordinated removal of executable code and obsolete CI/support claims after those gates passed.

## CI policy

CI follows support policy rather than repository file count:

- `reference`: deterministic PR/release evidence where the subsystem participates in the baseline;
- `supported`: maintained contract/integration lane; external profiles may be opt-in but fail closed when their compatibility claim is enabled;
- `experimental`: non-gating or scoped evaluation lanes unless a security regression check must remain gating for code that ships;
- `deprecated`: migration/compatibility evidence only until removal;
- `external`: core contract conformance tooling, not first-party upstream maintenance.

The Forge sidecar lane was removed with the executable retirement. Other required lanes remain unchanged. Future obsolete compatibility lanes should likewise be removed in the same retirement sequence rather than retained indefinitely.

## Extension rule

A new first-party implementation behind an audited boundary must update `ADAPTER_SUPPORT_MATRIX.toml` in the same change. It must either:

- be classified `experimental` while evidence is gathered;
- justify unique value and provide conformance evidence before becoming `supported`; or
- replace/deprecate another implementation with a migration plan.

Third-party/community implementations may continue to implement stable public contracts without being added to core. The matrix is therefore a statement of **first-party maintenance commitments**, not a closed provider allow-list.
