# First-Run Component Research Matrix

Status: initial verified research pass for issue #799  
Reviewed: 2026-09-11

This document is the research input for the provider-neutral first-run setup. It is **not** an adoption list and it does not grant install, activation, trust or execution authority. Existing canonical owner domains, the Registry/Marketplace lifecycle from #638, authorization/security policy, and evaluation decisions remain authoritative.

## Decision vocabulary

- `recommended` — preferred baseline/default boundary for the platform. This can be an internal platform-owned implementation rather than a third-party product.
- `supported` — suitable as an explicit adapter/provider when installed and compatible.
- `experimental/evaluate` — promising, but adoption needs focused evaluation before the wizard can recommend it automatically.
- `reject/defer` — do not offer as a normal first-run choice now. This may be due to architecture overlap, licensing, operational weight, project status, maturity, or cost-policy mismatch.

Unknown facts remain unknown. Popularity is not adoption evidence.

## Research rules

For first-run purposes a candidate matters only when it can sit behind a platform-owned contract without redefining canonical Task, Run, Agent, Workspace, File, Artifact, Tool, Node/Worker, Approval, Secret or Event identities.

The project cost rule remains strict: baseline operation must not require an additional paid AI API, model subscription or mandatory hosted service. Optional BYOK/hosted endpoints may exist, but they cannot be the only path for a supported category.

The first-run wizard should prefer the smallest stable baseline and expose advanced provider selection only where a real owner-domain provider seam already exists.

---

# 1. Orchestrators / Agent runtimes

| Candidate | License / hosting | Integration shape | Resource / platform notes | Security / lock-in notes | Classification | Rationale |
|---|---|---|---|---|---|---|
| Platform internal/minimal orchestrator | Project-owned | Native canonical contracts | Lowest baseline footprint; Linux/Windows/VPS | No third-party runtime lock-in | `recommended` | Ensures the platform can start with no external orchestrator and keeps contracts authoritative. |
| Hermes adapter | External upstream already in project architecture | Adapter behind platform orchestrator boundary | Depends on Hermes deployment | Provider-private state must not become canonical identity | `supported` when adapter is installed | Existing architecture target; should never be required for baseline startup. |
| LangGraph | MIT; local library/runtime paths | Python/JS orchestration framework | Local library; durable/stateful features may add persistence concerns | Strong framework semantics can overlap canonical Run/workflow ownership | `experimental/evaluate` | Mature agent orchestration reference, but adapter fit and ownership overlap must be tested before making it a first-run option. |
| Microsoft Agent Framework | MIT; local Python/.NET framework | SDK/framework adapter | Cross-language support; no mandatory hosted AI service | Framework workflow/session concepts may overlap platform identities | `experimental/evaluate` | Strong current candidate for interoperability/reference, not a default runtime. |
| Google ADK 2.x | Apache-2.0; local framework | SDK/framework adapter | Python plus other language variants; model-agnostic claim, but optimized around Gemini | Current 2.x line introduced breaking session/event changes; Google ecosystem affinity must not leak into canonical contracts | `experimental/evaluate` | Worth adapter evaluation, but not safe to promote directly into first-run recommendations. |
| Pydantic AI | MIT; local Python library | Python agent SDK/library | Lightweight Python fit | Library semantics overlap less than full platforms, but still requires explicit adapter mapping | `experimental/evaluate` | Good lightweight comparison candidate; needs proof that it adds value over platform-native orchestration. |
| AG2 | Apache-2.0 plus retained MIT-origin code; local | Multi-agent framework | Python; current project states transition toward v1.0 | Upstream framework transition raises short-term compatibility risk | `experimental/evaluate` | Useful reference candidate, but API churn makes automatic recommendation premature. |
| CrewAI / Agno and similar framework stacks | Open-source frameworks, self-hostable paths | SDK/framework adapters | Additional dependencies and framework-specific state | High overlap with platform-owned Agent/Run/Tool semantics | `reject/defer` for first-run | Keep in Marketplace/evaluation space unless a concrete interoperability requirement appears. |

Verified upstream references:

- LangGraph: https://github.com/langchain-ai/langgraph — MIT; stateful/durable agent orchestration.
- Microsoft Agent Framework: https://github.com/microsoft/agent-framework — MIT; Python/.NET agent and multi-agent workflows.
- Google ADK: https://github.com/google/adk-python — Apache-2.0; ADK 2.x currently documents breaking changes from 1.x.
- Pydantic AI: https://github.com/pydantic/pydantic-ai — MIT.
- AG2: https://github.com/ag2ai/ag2 — Apache-2.0 with original MIT code retained.

**Category decision:** expose one global default orchestrator, but allow multiple installed orchestrator adapters. Workspace/Agent overrides may be allowed only where the canonical orchestrator contract already supports them. Task-level switching should remain exceptional because it can change execution semantics materially.

---

# 2. Model provider / inference backends

| Candidate | License / hosting | API / standards | Hardware / OS | Classification | Rationale |
|---|---|---|---|---|---|
| Platform `ModelProvider` + OpenAI-compatible endpoint adapter | Project-owned | OpenAI-compatible endpoint plus platform-native provider contract | Remote or local; CPU/GPU depends endpoint | `recommended` | Stable product-neutral seam; avoids making a gateway or one inference engine canonical. |
| Ollama | MIT; local/self-hosted | HTTP API; OpenAI-compatible surfaces available | CPU/GPU; strong desktop/Linux usability | `supported` | Very good local-first UX, especially single-node/desktop; should be discovered, not required. |
| llama.cpp server | MIT; local/self-hosted | HTTP/OpenAI-compatible server modes | Excellent CPU path plus multiple GPU backends | `supported` | Best low-level portable local inference option and useful CPU fallback. |
| vLLM | Apache-2.0; self-hosted | OpenAI-compatible server | Primarily accelerator/server oriented; Linux strongest | `supported` | Strong throughput choice for GPU workers and multi-user serving. |
| SGLang | Apache-2.0; self-hosted | OpenAI-compatible serving paths | GPU/server focused; distributed features | `experimental/evaluate` | Strong current serving candidate, but should be benchmarked against vLLM on representative models/hardware before auto-recommendation. |
| LocalAI | MIT; self-hosted | OpenAI-compatible local API | CPU/GPU; broad local backends | `supported` | Useful broad compatibility layer, especially heterogeneous local setups; larger surface than llama.cpp/Ollama. |
| Arbitrary OpenAI-compatible remote endpoint | Endpoint-specific | OpenAI-compatible API | Remote | `supported` | Necessary interoperability seam, but trust/cost/security metadata must remain explicit and no paid endpoint may be required. |

Verified upstream references:

- Ollama: https://github.com/ollama/ollama
- llama.cpp: https://github.com/ggml-org/llama.cpp
- vLLM: https://github.com/vllm-project/vllm
- SGLang: https://github.com/sgl-project/sglang — Apache-2.0.
- LocalAI: https://github.com/mudler/LocalAI — MIT.

**Category decision:** multiple model providers may be active simultaneously. The first-run profile selects a default provider and default model, while Workspace/Agent/Task routing may override them through the canonical ModelRouter. Hardware fit is a compatibility fact, not a trust decision.

---

# 3. Model routing / gateways

| Candidate | License / hosting | Architecture | Security / operations | Classification | Rationale |
|---|---|---|---|---|---|
| Platform ModelRouter | Project-owned | In-process canonical routing policy | Smallest trust boundary and no extra network hop | `recommended` | Canonical routing semantics belong to the platform; external gateways remain optional adapters. |
| LiteLLM adapter | MIT/open-source self-host path | External proxy/gateway with broad provider support | Additional service, keys and policy surface; keep optional | `supported` | Already an explicit optional architecture path in #11. |
| Bifrost | Apache-2.0; local/Docker; OpenAI-compatible | Go gateway, provider routing, failover, plugins, optional clustering | Active releases; security policy exists; a 2026 high-severity advisory is recorded upstream, so version pinning/patch posture matters | `experimental/evaluate` | Strong current alternative to LiteLLM; merits focused gateway/security/latency evaluation before support classification. |
| Envoy AI Gateway | Apache ecosystem; self-host | Envoy/Kubernetes gateway architecture | Operationally heavier; attractive only where Envoy/K8s already exists | `experimental/evaluate` | Good infrastructure fit for a future K8s deployment profile, poor baseline choice. |
| Portkey open-source gateway/core | Mixed product context; self-host path exists | Gateway/guardrail/router | Vendor/product changes in 2026 and hosted/enterprise positioning increase governance review need | `reject/defer` for first-run | Do not create a baseline dependency; keep as external evaluation candidate if required. |
| Hosted-only routers/marketplaces | Proprietary/hosted | Remote gateway | Recurring third-party dependency/cost | `reject/defer` | Violates the baseline cost/independence goal as a required component. |

Verified upstream references:

- Bifrost: https://github.com/maximhq/bifrost — Apache-2.0; OpenAI-compatible; current 2026 releases.
- Bifrost security: https://github.com/maximhq/bifrost/security
- LiteLLM: https://github.com/BerriAI/litellm
- Envoy AI Gateway: https://github.com/envoyproxy/ai-gateway

**Category decision:** model gateway is **not** a mandatory first-run category. The wizard should show it only in Advanced mode when an external gateway adapter is installed. The default path remains the platform ModelRouter.

---

# 4. Executor / sandbox / execution provider

| Candidate | License / hosting | Isolation / execution shape | Platform notes | Classification | Rationale |
|---|---|---|---|---|---|
| Local/reference executor | Project-owned | Host/local reference execution under canonical policy | Lowest operational cost; must remain constrained by #15/#34/#37/#43 | `recommended` | Guarantees a baseline path and proves executor independence from Forge/sandbox products. |
| Forge adapter | External upstream already in project architecture | Adapter behind canonical Executor/Worker contracts | Provider-private lifecycle must stay inside adapter | `supported` when installed | Existing intended provider; not baseline authority. |
| Containarium | Existing project candidate | Container-oriented isolation provider | Requires separate security/egress/credential evaluation | `experimental/evaluate` | Keep optional until evidence supports a stable provider contract. |
| Agent-Sandbox | Apache-2.0 per #798 research scope; self-host; Kubernetes-native | Stateful high-isolation sandbox service | Significant Kubernetes/ops cost; #798 owns adoption evidence | `experimental/evaluate` | #799 may discover it, but must not pre-empt #798. |
| SWE-ReX | MIT; local/cloud backends | Sandboxed code execution abstraction; local and remote providers | Supports broad platforms including non-Linux paths; provider support varies | `experimental/evaluate` | Strong new candidate for a lighter adapter seam and cross-platform execution; needs isolation/credential/egress testing. |
| E2B self-host infrastructure | Self-hostable infra path | Sandbox service | Higher operational surface; upstream cloud compatibility can create product coupling | `experimental/evaluate` | Evaluate only if high-isolation/browser workloads justify it. |

Verified upstream reference:

- SWE-ReX: https://github.com/SWE-agent/SWE-ReX — MIT; local/cloud sandbox execution abstraction.

**Category decision:** one default executor per profile is useful, but multiple executors may be installed and selected by workload/policy. Task override is legitimate when the canonical scheduler/authorization layer approves the provider and target Worker.

---

# 5. Memory / knowledge / retrieval

| Candidate | License / hosting | Shape | Operations / lock-in | Classification | Rationale |
|---|---|---|---|---|---|
| SQLite/Postgres canonical persistence | Project-owned use of standard DBs | Structured durable memory/metadata | Minimal extra service when already present | `recommended` | Default semantic memory should not require a vector database or agent-memory framework. |
| pgvector | PostgreSQL-style open-source extension | Vector search inside Postgres | Reuses existing database; simpler operations | `supported` | Best first vector-search option when Postgres is already deployed. |
| Qdrant | Apache-2.0; self-host | Dedicated vector database | Additional service but focused and operationally approachable | `supported` | Good dedicated retrieval provider without mandatory hosted dependency. |
| Chroma | Apache-2.0; local/self-host | Embedded/server vector store | Simple local developer path; scalability envelope differs from Qdrant/Milvus | `supported` for local/dev | Useful local profile option; not necessarily the multi-node default. |
| Weaviate | BSD-3-Clause; self-host | Vector/hybrid retrieval service | Larger feature/ops surface | `experimental/evaluate` | Useful hybrid-search candidate; first-run recommendation needs workload evidence. |
| Milvus | Apache-2.0; self-host | Distributed vector DB | Higher deployment complexity | `reject/defer` for baseline | Valuable at large scale, disproportionate for default VPS/local setup. |
| Mem0 | Open-source memory project with hosted options | Agent-memory layer | Adds opinionated memory semantics above storage | `experimental/evaluate` | Evaluate as an optional semantic memory adapter, never as canonical persistence. |
| Graphiti | Open-source graph-memory project | Temporal/graph knowledge memory | Requires graph/search infrastructure decisions | `experimental/evaluate` | Potential knowledge provider; needs domain and operational evaluation. |
| Letta | Open-source agent-memory/runtime project | Stateful agent memory/runtime | High overlap with Agent/runtime ownership | `experimental/evaluate` | Treat as specialized adapter/reference, not a default memory database. |
| OpenViking | AGPL-3.0; self-host | Context/memory system | Copyleft and additional architecture surface | `experimental/evaluate` | Existing catalog candidate; licensing and integration scope require deliberate review. |

**Category decision:** first run selects a durable metadata store only when deployment setup actually needs it; a separate vector/knowledge provider is optional. Workspace/Agent overrides are reasonable for knowledge sources, while Task override should select retrieval context rather than migrate durable canonical memory implicitly.

---

# 6. Tools / MCP / capability layer

| Candidate | License / hosting | Shape | Classification | Rationale |
|---|---|---|---|---|
| Canonical Capability Registry + native tools | Project-owned | Native platform contract | `recommended` | Canonical tool identity, authorization and evidence must remain platform-owned. |
| MCP client/server transport | MCP specification ecosystem | Standard protocol adapter | `supported` | Important interoperability mechanism, but MCP does not become canonical authorization or Registry identity. |
| MCP Registry | Currently transitioning MIT -> Apache-2.0 for code/spec contributions | Discovery source | `supported` as discovery-only | Useful discovery source; must never become automatic trust/install authority. |
| FastMCP | Open-source library/framework | MCP implementation helper | `supported` as implementation dependency where useful, not a provider | A library is not a user-facing first-run provider choice. |
| Framework-specific tool registries | Framework-specific | SDK registries | `reject/defer` for first-run | Adapters may translate them, but the wizard should not duplicate the canonical Capability Registry. |

Verified upstream reference:

- MCP Registry: https://github.com/modelcontextprotocol/registry — licensing transition documented upstream; discovery must remain untrusted until normal curation/security gates pass.

**Category decision:** the wizard exposes whether MCP integrations are enabled/configured and which MCP servers are discovered, not a replacement `Tool Provider` default that bypasses the Capability Registry.

---

# 7. Files / artifact storage

| Candidate | License / hosting | S3 / distribution | Operations | Classification | Rationale |
|---|---|---|---|---|---|
| Local filesystem | Platform-owned adapter | Local file semantics | Lowest footprint | `recommended` | Correct baseline for local/single-node profile. |
| Canonical S3-compatible adapter | Project-owned boundary | S3 API | Backend-independent | `recommended` for remote/object-store seam | Keeps object-store product choice outside canonical File/Artifact identity. |
| SeaweedFS | Apache-2.0; self-host | S3 plus file-system/distributed storage | Mature distributed storage but more components than a single local filesystem | `supported` | Good current self-hosted S3 candidate for multi-node installations. |
| RustFS | Apache-2.0; self-host | S3-compatible distributed object store | Current upstream is active but still reports beta-version package metadata | `experimental/evaluate` | Promising MinIO successor; needs durability/compatibility/upgrade benchmarking before recommendation. |
| Garage | AGPL-3.0; self-host | S3-compatible, small/geo-distributed focus | Lightweight and resilient for small clusters | `experimental/evaluate` | Strong topology fit for multiple small VPS nodes, but AGPL boundary and S3 compatibility scope require review. |
| Ceph RGW | LGPL-family/project licenses; self-host | S3/object + block/file platform | Heavy operational footprint | `reject/defer` for baseline | Appropriate only when Ceph already exists or storage scale justifies it. |
| MinIO Community Edition | Former AGPL community project | S3-compatible | Community repository was archived/read-only in 2026 | `reject/defer` for new installs | Existing deployments may be supported for compatibility, but do not recommend for new first-run setup. |

Verified upstream references:

- SeaweedFS: https://github.com/seaweedfs/seaweedfs — Apache-2.0; active distributed S3/file storage.
- RustFS: https://github.com/rustfs/rustfs — Apache-2.0; active; current workspace metadata reports a beta release line.
- Garage: https://github.com/deuxfleurs-org/garage — AGPL-3.0; self-hosted small-to-medium geo-distributed S3 focus.

**Category decision:** local filesystem is the local profile default. Multi-node/object-storage profiles select an S3-compatible endpoint, while the backend product remains an implementation detail wherever possible.

---

# 8. Event transport / messaging

| Candidate | License / hosting | Semantics | Operations | Classification | Rationale |
|---|---|---|---|---|---|
| Internal/in-process transport | Project-owned | Baseline events for single process/node | Lowest footprint | `recommended` | No external broker should be required for first boot. |
| NATS + JetStream | Apache-2.0; self-host | Messaging + persistence/streaming | Small operational footprint relative to Kafka-class systems; cluster capable | `supported` | Best current optional distributed transport candidate for VPS/multi-node deployments. |
| RabbitMQ | MPL-2.0; self-host | Mature AMQP/streams broker | More operational surface than NATS but mature | `supported` | Useful compatibility/enterprise alternative, not default for local mode. |
| Redis Streams | Redis 8 tri-license allows AGPLv3/RSALv2/SSPLv1 choice | Stream/message semantics on Redis | Attractive if Redis already exists; license choice and coupling matter | `experimental/evaluate` | Reuse can reduce services, but messaging should not force Redis into baseline architecture. |
| Kafka/Redpanda-class systems | Self-host options | High-throughput durable log | Heavy for target VPS/local baseline | `reject/defer` | Add only if measured workloads need them. |

Verified upstream references:

- NATS server: https://github.com/nats-io/nats-server — Apache-2.0.
- RabbitMQ: https://github.com/rabbitmq/rabbitmq-server — MPL-2.0 for server/core plugins.
- Redis: https://github.com/redis/redis — Redis 8.x upstream documents RSALv2/SSPLv1/AGPLv3 tri-license choice.

**Category decision:** messaging is **not** a normal first-run user choice. Local profiles use the internal transport. A distributed deployment profile may select/configure a broker when #35/#14 topology requires it.

---

# 9. Workflow / durable execution

#21 remains authoritative. #799 records options for compatibility/discovery only and must not decide the workflow engine independently.

| Candidate | License / hosting | Operational model | Classification for #799 | Notes |
|---|---|---|---|---|
| No external workflow engine | Project-owned kernel | Existing platform semantics | `recommended` baseline | Must remain possible. |
| Temporal | MIT; self-host | Dedicated durable execution service | `experimental/evaluate` under #21 | Mature reference baseline; operationally significant. |
| DBOS | MIT Python library; Postgres-backed | Embedded/library durability | `experimental/evaluate` under #21 | Interesting lower-ops alternative where Postgres already exists. |
| Hatchet | MIT; self-host | Dedicated workflow/worker platform | `experimental/evaluate` under #21 | Promising but overlaps scheduler/worker ownership; needs exact seam analysis. |
| Restate | BSL 1.1 with future Apache-2.0 change; self-host production permitted within additional-use terms | Dedicated durable execution/runtime | `reject/defer` pending licensing/architecture decision | Current license explicitly says BSL is not an Open Source license; do not classify as OSS. |
| Airflow/Prefect/Kestra-style data workflow systems | Mixed OSS/self-host | General workflow/data orchestration | `reject/defer` for #799 | Different primary abstraction; avoid expanding first-run surface without a #21 result. |

Verified upstream references:

- Temporal: https://github.com/temporalio/temporal — MIT.
- DBOS Python: https://github.com/dbos-inc/dbos-transact-py — MIT; Postgres-backed library architecture.
- Hatchet: https://github.com/hatchet-dev/hatchet — MIT.
- Restate: https://github.com/restatedev/restate — BSL 1.1 at review date; additional-use grant permits many internal/self-hosted deployments but the license itself is not OSI open source.

**Category decision:** do not show a workflow engine selector in the normal wizard until #21 chooses whether an external durable layer belongs in the supported platform architecture.

---

# 10. Observability / evaluation

| Candidate | License / hosting | Role | Classification | Rationale |
|---|---|---|---|---|
| OpenTelemetry + platform-owned observability contracts | Open standard / project-owned integration | Traces, metrics, logs, provenance | `recommended` | Vendor-neutral baseline and best contract boundary. |
| Langfuse OSS core | MIT outside enterprise directories; self-hostable | LLM/agent observability and evaluation | `supported` optional | Strong self-host path; enterprise-only features must stay optional and explicit. |
| Opik | Apache-2.0; self-hostable | LLM/agent observability + evaluation | `supported` optional | Permissive fully self-hostable candidate; useful alternative to Langfuse. |
| Arize Phoenix | Elastic License 2.0; self-hosting allowed | OpenTelemetry/OpenInference observability + evaluation | `experimental/evaluate` | Technically strong, but ELv2 is source-available rather than OSI open-source; license scope should stay explicit. |
| Promptfoo / DeepEval / Inspect AI | OSS evaluation tools | Evaluation runners/frameworks | `supported` behind #19 where applicable | These are evaluation engines/tools, not a replacement for canonical observability storage. |
| Hosted-only observability SaaS | Proprietary | Remote tracing/eval | `reject/defer` as required path | Optional integrations may exist, but no baseline dependency or mandatory subscription. |

Verified upstream references:

- Langfuse: https://github.com/langfuse/langfuse — MIT core with separately licensed enterprise directories; documented self-hosting.
- Opik: https://github.com/comet-ml/opik — Apache-2.0; self-hostable.
- Phoenix: https://github.com/Arize-ai/phoenix — ELv2; upstream docs permit free self-hosting but restrict managed-service use.

**Category decision:** observability/evaluation is not a mandatory first-run provider selector. OpenTelemetry/platform evidence remains canonical. Advanced settings may configure an exporter/backend and #19 evaluation providers separately.

---

# Cross-category recommendations

## Recommended baseline profile building blocks

```text
Orchestrator:        internal/minimal platform path
Model routing:       platform ModelRouter
Model provider:      discovered platform ModelProvider; prefer local-compatible provider when available
Executor:            local/reference executor
Memory/persistence:  canonical SQLite/Postgres path
Tools:               canonical Capability Registry; MCP optional
Files:               local filesystem
Messaging:           internal transport
Workflow engine:     none externally required
Observability:       OpenTelemetry/platform-owned evidence
```

This baseline deliberately works without Hermes, Forge, Ollama, LiteLLM, Redis, NATS, a vector database, a workflow engine or a hosted service.

## Supported alternatives suitable for explicit adapters

- Hermes where installed and healthy.
- Ollama, llama.cpp, vLLM, LocalAI and arbitrary compatible endpoints through ModelProvider adapters.
- LiteLLM as an optional gateway adapter.
- Forge as an optional execution provider.
- pgvector/Qdrant/Chroma where retrieval is configured.
- MCP servers behind the canonical Capability Registry.
- SeaweedFS or other validated S3-compatible endpoints for object storage.
- NATS JetStream or RabbitMQ only when distributed topology needs an external broker.
- Langfuse/Opik as optional observability/evaluation backends behind canonical telemetry/evaluation contracts.

## Evaluation candidates created/required from this pass

The following deserve focused evaluation before automatic first-run recommendation:

1. **Bifrost** — compare to optional LiteLLM gateway on provider coverage, OpenAI compatibility, latency, secrets, SSRF/egress handling, clustering, upgrades and security patch posture.
2. **SGLang** — compare to vLLM on representative local models/GPUs, OpenAI compatibility, distributed serving and operational complexity.
3. **SWE-ReX** — compare to reference/Forge/Containarium paths on isolation, Workspace mapping, Windows/Linux behavior, credentials, egress, artifacts and cancellation.
4. **RustFS** — validate S3 behavior needed by canonical Artifact/File storage, durability, upgrade/recovery, multi-node failure behavior and maturity before recommending over SeaweedFS.
5. **Garage** — evaluate AGPL implications plus S3 feature fit and multi-VPS resilience.
6. **DBOS** — evaluate only under #21 against Temporal/no-engine baseline, especially Postgres coupling, recovery semantics and canonical Run ownership.
7. **Opik vs Langfuse vs Phoenix** — determine whether #16/#19 need an officially supported optional self-hosted backend at all, and if so what minimum adapter contract is justified.
8. **LangGraph / Microsoft Agent Framework / Google ADK / Pydantic AI** — interoperability evaluation should focus on whether an adapter can preserve canonical Agent/Task/Run/Tool identities rather than importing framework-native ownership into the core.

# Rejected/deferred defaults from this pass

- MinIO Community Edition for new installs: archived/read-only upstream status in 2026 makes it unsuitable as a recommended new object-store default.
- Ceph for baseline/local setup: disproportionate operational footprint unless already deployed.
- Restate as an "open-source" recommendation: current BSL 1.1 is source-available with an additional-use grant, not OSI open source.
- Hosted-only model gateways/routers/observability services as required components: incompatible with baseline independence and cost policy.
- Framework-specific tool registries as parallel canonical capability systems.
- External workflow selector in the normal first-run UI before #21 concludes.

# Provider-boundary conclusion for the wizard

The normal first-run UI should expose only choices that materially affect a new installation and already have canonical seams:

1. setup profile (`auto`, `local`, `multi-node`, `advanced`);
2. default model provider + default model;
3. default executor/sandbox when more than one compatible provider is available;
4. memory/knowledge option only when a non-baseline provider is installed/desired;
5. storage target (`local` vs validated object-store endpoint) where deployment topology requires it;
6. compute/Worker target or policy for multi-node installs;
7. MCP/tool discovery enablement and selected discovered servers, routed through Capability Registry;
8. orchestrator only in Advanced mode when an external orchestrator adapter is installed.

Do **not** make users choose messaging, workflow engine, model gateway, observability backend, database engine or every installed framework during ordinary first run. Those belong to deployment/advanced settings and should be inferred/configured from topology unless a concrete owner-domain reason requires explicit selection.

# Compatibility facts the resolver must model

At minimum:

- provider installed / reachable / version;
- required adapter installed;
- required OS/architecture;
- CPU/GPU/accelerator type and available memory;
- model format/backend compatibility;
- required container/Kubernetes/runtime availability;
- remote endpoint TLS/auth availability without serializing credentials;
- Worker/Node reachability and capability class;
- object-store API capability subset required by File/Artifact operations;
- messaging/workflow infrastructure only when the selected deployment profile requires it;
- provider lifecycle (`supported`, `experimental`, `deprecated`, `unavailable`);
- source/license/trust metadata reference from the canonical Registry/catalog where present;
- explicit security blockers from canonical policy.

The resolver reports facts and constraints. Authorization, trust approval and secret release remain owned by #15/#34/#43.

# Sources reviewed in this pass

In addition to the official upstreams linked above, the research cross-checked the existing project catalog and curation rules from #638. The catalog remains the canonical discovery/reference inventory; this document narrows that wider ecosystem into **first-run provider relevance**.

No project is promoted to adopted status by this document.