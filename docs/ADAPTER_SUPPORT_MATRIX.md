# Adapter support matrix

Issue: #904

The machine-readable source of truth is [`ADAPTER_SUPPORT_MATRIX.toml`](ADAPTER_SUPPORT_MATRIX.toml). This document defines how those entries are interpreted and records the consolidation decisions behind them.

## Why this exists

A platform-owned interface and a concrete implementation are different compatibility promises. The repository can keep a stable `Executor`, `ModelProvider`, `CapabilityToolProvider`, `BrowserProvider`, `RepositoryProvider`, or other replaceable boundary without promising that every implementation ever evaluated is maintained as a production option.

This matrix therefore answers three separate questions:

1. Does a stable platform boundary exist?
2. Does a first-party implementation exist?
3. What support claim, if any, does the project make for that implementation?

The answer to (1) must never be inferred from the current number of implementations. Removing or demoting an adapter does not make its vendor/runtime canonical.

## Support tiers

| Tier | Meaning | Compatibility expectation |
| --- | --- | --- |
| `reference` | Platform-owned deterministic/local baseline used to prove the contract and keep the reference product functional. | Must have maintained deterministic evidence. It must not require a recurring paid service. |
| `supported` | Maintained optional implementation with a real product use/profile and an identified contract, integration, or #46-style conformance path. | A compatibility claim is valid only for the concrete profile/version whose evidence passes. |
| `experimental` | First-party implementation kept for evaluation, prerelease protocol work, or evidence gathering. | Opt-in. Existence and unit/contract tests do **not** constitute a production compatibility claim. |
| `deprecated` | First-party implementation scheduled for removal or consolidation. | Must publish a migration path and removal criterion/target before deletion. |
| `external` | Public-contract implementation maintained outside core. | Core preserves the contract where versioning policy allows, but does not own implementation compatibility. |

Support tier is a **product/maintenance policy**, not provider runtime state. It therefore does not belong in `ProviderDescriptor`; that contract remains backend-neutral technical metadata.

## Audited scope

The #904 audit covers product-facing replaceable implementation boundaries:

- planner and orchestrator;
- executor;
- model provider and model router;
- capability/tool provider and MCP client transport;
- browser provider;
- File/Memory/Knowledge providers;
- durable/reference persistence repositories;
- message transport;
- connector and repository providers;
- observability exporters;
- verification and evaluation providers;
- security-evidence adapters where a first-party external implementation exists.

The matrix does **not** create separate compatibility claims for implementation helpers that merely decorate, transport, hydrate, or compose one of those providers. Examples include HTTP transport classes, observability wrappers such as `ObservedExecutor`, capability bridges, deployment composition roots, and test fakes. Those helpers inherit the support status of the product-facing implementation they serve unless they expose an independently selectable public compatibility profile. The MCP clients are listed separately because protocol-revision/transport compatibility is independently claimed and tested.

## Current supported surface

The complete entry-by-entry inventory, owner, burden, security surface, upstream state, use and evidence mapping is in the TOML file. The product-level summary is:

| Boundary | Reference baseline | Supported optional | Experimental / non-claiming |
| --- | --- | --- | --- |
| Planner | `planner.reference` | `planner.model-backed` | — |
| Orchestrator | `orchestrator.reference` | `orchestrator.hermes` | — |
| Executor | `executor.reference` | `executor.forge` | `executor.agent-sandbox`, `executor.swe-rex` |
| Model provider | `model.openai-compatible` | `model.litellm` | dedicated Bifrost/SGLang adapters intentionally absent |
| Model router | `model.router` | — | — |
| Capability/tool | `capability.native` | `capability.mcp` | — |
| MCP client transport | — | `mcp.python-sdk` | `mcp.stateless-http` |
| Browser | `browser.stdlib` | — | — |
| File | `data.file.local` | — | — |
| Memory | `data.memory.local` | — | — |
| Knowledge | `data.knowledge.local` | — | — |
| Kernel persistence | `persistence.kernel.in-memory`, `persistence.kernel.sqlite` | — | — |
| Message transport | `messaging.in-process` | `messaging.tcp` | — |
| Connector | `connector.reference` | `connector.github-releases` | — |
| Repository/Git | `repository.local-git` | `repository.connector` | — |
| Observability exporter | `observability.noop`, `observability.in-memory` | `observability.accounting-bridge` | — |
| Verification | `verification.completion` | — | — |
| Evaluation | `evaluation.deterministic-assertions`, `evaluation.metric-threshold` | — | — |
| Security evidence | — | — | `security-evidence.skillspector` |

There are currently **no active `deprecated` entries**. #904 does not require deleting an implementation to meet a count target; it requires every overlap either to have unique value or to be consolidated/demoted. The audit found several places where the correct reduction is to reduce compatibility claims or prevent a duplicate adapter from being introduced, not to delete working code.

## Consolidation decisions

### 1. One OpenAI-compatible HTTP model path, not one adapter per gateway/runtime

`OpenAICompatibleModelProvider` is the broad local/self-hosted reference path. LiteLLM deliberately reuses that transport stack and remains a supported optional adapter only for the extra LiteLLM configuration/identity semantics.

Bifrost and SGLang evaluation do not justify dedicated first-party `ModelProvider` implementations while their supported profile is expressible through the OpenAI-compatible boundary. A dedicated adapter may be added only when a reviewed, supported capability cannot be represented cleanly through the standard boundary. This prevents a gateway-per-file adapter explosion.

### 2. Forge HTTP is transport, not a second executor

`ForgeExecutor` is the compatibility surface. `ForgeClient`/HTTP sidecar transport is an implementation detail beneath it. Documentation and CI claims must talk about the Forge executor profile rather than counting the HTTP client as another Executor implementation.

### 3. MCP has one capability adapter and profile-specific clients

`MCPToolProvider` is the canonical capability-side adapter. `MCPPythonSDKClient` is the supported stable MCP client profile. `MCPStatelessHTTPClient` is deliberately `experimental` while its upstream protocol/conformance line is prerelease and informational.

This means the codebase does **not** support “three MCP tool providers.” It supports one MCP capability projection with one stable claimed client profile and one prerelease experimental client profile.

### 4. In-process and TCP messaging are both justified

`InProcessMessageTransport` and `TcpMessageTransport` share the same transport contract but are not redundant: one proves deterministic same-process behavior; the other provides the self-hosted cross-process topology used by distributed workers. Removing either would eliminate a distinct supported deployment shape rather than merely reduce duplicate code.

### 5. NoOp and in-memory observability are both reference modes

`NoOpExporter` proves that telemetry backends are optional and cannot become lifecycle authority. `InMemoryExporter` provides deterministic retained telemetry/timeline evidence. Their different retention semantics justify both. `AccountingBridgeExporter` is a semantic handoff to accounting, not a third general monitoring backend.

### 6. Local Git and connector-backed repositories are complementary

`LocalGitRepositoryProvider` executes local Git operations. `ConnectorRepositoryProvider` maps an already-supported Connector into repository semantics for remote collaboration. The latter is a bridge over the connector boundary, not a second canonical repository model.

### 7. External executor experiments do not inherit production support from contract tests

`AgentSandboxExecutor` and `SwerexExecutor` implement the canonical Executor and have deterministic tests, but neither is therefore automatically supported. Agent-Sandbox remains evidence-gated by its live isolation evaluation; SWE-ReX is explicitly evaluation/experimental-only. They must not be included in a generic “supported executors” claim or required reference CI lane.

### 8. Helper variants inherit the parent support claim

The following classes/patterns do not get independent product tiers unless they later expose independently selectable compatibility semantics:

- Forge/Hermes HTTP transport helpers;
- `DurableGitHubReleaseConnectorProvider`, which hydrates the supported GitHub Releases connector from canonical persistence;
- `PlanningOrchestratorAdapter`, which bridges canonical activated plans into the Orchestrator seam;
- `ExecutorLifecycleBackend` and distributed lifecycle wrappers;
- `BoundBrowserProvider`;
- connector/repository capability bridges;
- `Observed*` instrumentation decorators;
- deployment app/composition-root modules;
- testing fakes and benchmark-only fault providers.

Treating these as separate providers would inflate the support matrix without creating a distinct user-selectable implementation promise.

## Compatibility and conformance rule

An implementation can be `supported` only when the matrix names an evidence path. For external/upstream-backed implementations, the claim is scoped to the tested profile/revision; “adapter exists” is not enough.

The platform-wide #46 layer remains the aggregator for end-to-end compatibility claims. Focused contract/integration tests remain owned by their subsystem. #904 does not duplicate those suites: it maps support claims to them and fails closed when a supported entry lacks evidence.

In particular:

- the reference baseline must remain deterministic, local-first, and free from mandatory paid services;
- Hermes/Forge claims require their prepared external profiles;
- LiteLLM requires its dedicated compatibility lane;
- stable MCP claims require both protocol and platform evidence for the same profile;
- experimental implementations may have tests without becoming a compatibility claim.

## Deprecation/removal procedure

When a future audit moves an entry to `deprecated`, the same change must add:

1. the surviving target implementation or contract-level alternative;
2. migration guidance for configuration and persisted provider references if any;
3. the exact compatibility/CI lane that stops being maintained;
4. an explicit removal criterion or target release/date;
5. proof that canonical IDs/data do not depend on provider-private state;
6. a contract test demonstrating that an external/community replacement remains possible where the public boundary is still supported.

Only after those conditions are satisfied may the implementation and obsolete CI lane be removed.

## CI policy

CI follows support policy rather than repository file count:

- `reference`: deterministic PR/release evidence where the relevant subsystem participates in the baseline;
- `supported`: maintained contract/integration lane; external profiles may be opt-in but fail closed when their compatibility claim is enabled;
- `experimental`: non-gating or scoped evaluation lanes unless a security regression check must remain gating for code that ships;
- `deprecated`: only migration/compatibility evidence needed until removal;
- `external`: core contract conformance tooling, not first-party upstream maintenance.

No existing required lane is removed by this first audit because the overlapping implementations that remain supported have distinct product value. The reduction delivered here is the removal of ambiguous equal-support claims and the explicit prevention of redundant dedicated adapters (notably model gateways) where a broader supported boundary already covers the use case.

## Extension rule

A new first-party implementation behind an audited boundary must update `ADAPTER_SUPPORT_MATRIX.toml` in the same change. It must either:

- be classified `experimental` while evidence is gathered;
- justify unique value and provide conformance evidence before becoming `supported`; or
- replace/deprecate another implementation with a migration plan.

Third-party/community implementations may continue to implement stable public contracts without being added to core. The matrix is therefore a statement of **first-party maintenance commitments**, not a closed provider allow-list.
