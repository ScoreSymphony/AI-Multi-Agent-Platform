# Adapter support matrix

Issue: #904

The machine-readable source of truth is [`ADAPTER_SUPPORT_MATRIX.toml`](ADAPTER_SUPPORT_MATRIX.toml). This document defines how those entries are interpreted and records the consolidation decisions behind them.

## Policy

A platform-owned interface and a concrete implementation are different compatibility promises. Removing or demoting an implementation must never make a surviving vendor/runtime canonical. Support tiers describe product maintenance commitments, not runtime state:

| Tier | Meaning |
| --- | --- |
| `reference` | Platform-owned deterministic/local baseline used to prove a contract. |
| `supported` | Maintained optional implementation with profile-specific conformance evidence. |
| `experimental` | First-party evaluation implementation; existence/tests are not a production claim. |
| `deprecated` | First-party implementation scheduled for removal and carrying an explicit migration/removal plan. |
| `external` | Public-contract implementation maintained outside core. |

The audit covers planner/orchestrator, executor, model provider/router, capabilities and MCP clients, browser, File/Memory/Knowledge, persistence, messaging, connectors/repositories, observability, verification/evaluation, security evidence, and deployment lifecycle bridges.

## Current first-party surface

| Boundary | Reference | Supported | Experimental / other |
| --- | --- | --- | --- |
| Planner | `planner.reference` | `planner.model-backed` | — |
| Orchestrator | `orchestrator.reference` | `orchestrator.hermes` | — |
| Executor | `executor.reference` | — | `executor.agent-sandbox`, `executor.openshell`, `executor.swe-rex` |
| Model provider | `model.openai-compatible` | `model.litellm` | — |
| Model router | `model.router` | — | — |
| Capability/tool | `capability.native` | `capability.mcp` | — |
| MCP client | — | `mcp.python-sdk` | `mcp.stateless-http` |
| Browser | `browser.stdlib` | — | — |
| File | `data.file.local` | — | — |
| Memory | `data.memory.local` | — | — |
| Knowledge | `data.knowledge.local` | — | — |
| Single-node persistence topology | `persistence.single-node-topology` | — | — |
| Kernel repository | `persistence.kernel.in-memory`, `persistence.kernel.sqlite` | — | — |
| Agent repository | `persistence.agent.in-memory`, `persistence.agent.json` | — | — |
| Messaging | `messaging.in-process` | `messaging.tcp` | — |
| Connector | `connector.reference` | `connector.github-releases` | — |
| Repository/Git | `repository.local-git` | `repository.connector` | — |
| Observability | `observability.noop`, `observability.in-memory` | `observability.accounting-bridge` | — |
| Verification | `verification.completion` | — | — |
| Evaluation | `evaluation.deterministic-assertions`, `evaluation.metric-threshold` | — | — |
| Security evidence | — | `security-evidence.skillspector` | — |
| Deployment lifecycle | — | `deployment.distributed-lifecycle` | — |

## Forge retirement (#991)

Forge is no longer a first-party Executor implementation. The former `executor.forge` entry, Python adapter/HTTP transport, pinned Rust sidecar CI lane, Forge-only tests, and active adapter documentation were removed after the #889 golden path and #46 conformance gates were complete and backend-neutral/reference coverage carried the generic guarantees.

This removal does not promote another executor. `ReferenceExecutor` remains the baseline. Agent-Sandbox, OpenShell and SWE-ReX remain experimental and must earn any stronger support tier independently under this policy. Historical Forge ADR/reuse/provenance material is retained only to explain prior architectural decisions; it is not an active support claim.

See [`integrations/FORGE_RETENTION_DECISION.md`](integrations/FORGE_RETENTION_DECISION.md) and ADR 0013 for the evidence and guarantee-preservation record.

## Consolidation rules

- OpenAI-compatible HTTP remains the broad model-provider path; LiteLLM is supported only for its distinct gateway/profile semantics.
- MCP exposes one capability projection (`capability.mcp`) with profile-specific client transports (`mcp.python-sdk`, `mcp.stateless-http`).
- `messaging.in-process` and `messaging.tcp` are both justified because they support distinct deployment shapes.
- Persistence records the supported physical topology separately from interchangeable repository implementations.
- `observability.noop` and `observability.in-memory` are distinct reference modes; `observability.accounting-bridge` is a semantic bridge, not a lifecycle authority.
- `repository.local-git` and `repository.connector` are complementary local/remote collaboration implementations.
- Contract tests do not promote experimental executors to supported status.
- `security-evidence.skillspector` is supported only for its evaluated static/no-LLM/network-none profile and remains advisory.
- `deployment.distributed-lifecycle` is explicit because it selects an independently supported distributed Worker topology.

Helpers that merely transport, decorate, hydrate or compose a listed provider inherit the parent support claim unless they expose independently selectable compatibility semantics.

## Compatibility and CI rule

A `supported` implementation must name current evidence in the TOML matrix. External/upstream-backed claims are profile/revision-specific. The #46 platform conformance layer aggregates end-to-end claims; focused subsystem suites remain authoritative for their own contracts.

CI follows support policy rather than repository file count: reference paths remain deterministic and local-first, supported integrations keep maintained compatibility evidence, experimental paths remain scoped, and obsolete/deprecated lanes are removed with the implementation they served. Forge therefore has no active CI compatibility lane after #991.

## Deprecation/removal procedure

Before deleting a deprecated first-party implementation, record the surviving contract-level alternative, migration guidance, obsolete CI lane, removal criterion, identity/data independence, and replacement/public-contract evidence. When those conditions are met, remove the implementation and stale active-path docs/config/tests together.

Third-party/community implementations may continue to implement stable public contracts without being listed here. The matrix is a statement of first-party maintenance commitments, not a closed provider allow-list.
