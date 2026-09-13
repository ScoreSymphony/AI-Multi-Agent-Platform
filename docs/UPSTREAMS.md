# Upstream Component Registry

This document is the canonical inventory for architecture-significant third-party integrations used by the AI Multi-Agent Platform. A component is considered integrated only when its provenance, implementation and evidence support that claim; historical references do not make a removed component active.

## Required fields

Architecture-significant entries record purpose, status, integration categories, canonical upstream, pinned/reviewed revision, license, review date, platform boundary, source/modification provenance, compatibility/security/deployment constraints, baseline/cost impact, update method, exit strategy and any material ADR.

Allowed lifecycle statuses are **candidate**, **approved**, **integrated**, **deprecated** and **removed**. `removed` means the platform no longer uses the component; provenance may remain for historical/license/architecture evidence.

Machine-readable provenance lives under `upstream/*.yaml`.

## Current architecture-significant upstreams

### jsonschema

- **Purpose:** Draft 2020-12 JSON Schema validation for canonical capability contracts.
- **Status:** integrated through #12.
- **Categories:** runtime library dependency.
- **Upstream:** `https://github.com/python-jsonschema/jsonschema`; pinned package `4.26.0`.
- **License / review:** MIT; verified/reviewed 2026-09-02.
- **Boundary:** internal validation behind platform-owned capability types; no upstream types are canonical API types.
- **Local source / modification:** no copied or modified upstream source.
- **Compatibility/security/deployment:** Python >=3.12 platform baseline; in-process validation only; no service, credentials, GPU or external infrastructure.
- **Baseline / recurring cost:** required for canonical capability schema validation; no recurring paid service.
- **Update:** explicit dependency update plus release/security/license review and capability/full CI.
- **Exit:** replace the validator while preserving platform-owned capability contracts.
- **Adoption review:** `docs/upstream/JSONSCHEMA_ADOPTION.md`.

### Model Context Protocol Python SDK

- **Purpose:** optional concrete MCP stdio and Streamable HTTP transport.
- **Status:** integrated through #12.
- **Categories:** optional adapter/library dependency.
- **Upstream:** `https://github.com/modelcontextprotocol/python-sdk`; `2.1.1`.
- **License / review:** MIT; reviewed 2026-09-02.
- **Boundary:** `ai_multi_agent_platform.adapters.mcp_sdk` implements the platform-owned `MCPClient` protocol.
- **Local source / modification:** none; package dependency only.
- **Compatibility/security/deployment:** explicit v2.1.1 profile; stdio may start a configured process and HTTP performs configured egress. Secrets must not enter canonical metadata/audit payloads.
- **Baseline / recurring cost:** optional; no recurring paid service.
- **Update:** explicit pinned-version review plus real stdio/optionality/full CI.
- **Exit:** replace `MCPClient` transport without canonical Task/Agent/data migration.
- **Adoption review:** `docs/upstream/MCP_PYTHON_SDK_ADOPTION.md`.

### Model Context Protocol Conformance Test Suite

- **Purpose:** official wire-level MCP protocol compatibility evidence.
- **Status:** approved test/evidence integration through #731; not a runtime dependency.
- **Categories:** protocol/specification test tooling.
- **Upstream:** `https://github.com/modelcontextprotocol/conformance`; stable `v0.1.16` / `21a9a2febd7100d7c17ac1021ee7f2ed9f66a1e0`; prerelease informational `0.2.0-alpha.11` / `a983ba93c91e0bb31d0b6849eeb52f0ad1083107`.
- **License / review:** MIT; reviewed 2026-09-11.
- **Boundary:** evidence-only code under `conformance/mcp/`, CI scripts and `ai_multi_agent_platform.conformance.mcp_protocol`.
- **Local source / modification:** no vendored source; exact reviewed checkout only.
- **Compatibility/security/deployment:** stable claim limited to the tested `2025-11-25` client/tool Streamable-HTTP profile; newer stateless evidence remains informational. Local fixtures require no production credentials or paid service.
- **Baseline / recurring cost:** optional; none.
- **Update:** explicit pin update and separate protocol/platform conformance review.
- **Exit:** remove suite/evidence integration without runtime or persisted-data migration.
- **Provenance:** `upstream/mcp-conformance.yaml`.

### LiteLLM

- **Purpose:** optional model-gateway compatibility layer behind platform-owned `ModelProvider` and `ModelRouter` boundaries.
- **Status:** integrated through #11.
- **Categories:** optional adapter/library; optional external service.
- **Upstream:** `https://github.com/BerriAI/litellm`; `v1.99.0` / `fa647f742d7baefe8eb1181899d9c81b41559772`.
- **License / review:** MIT outside `enterprise/`; enterprise code is excluded; reviewed 2026-09-03.
- **Boundary:** `LiteLLMModelProvider`; proxy mode reuses the OpenAI-compatible transport. Canonical routing remains platform-owned.
- **Local source / modification:** platform adapter only; no upstream source copied.
- **Compatibility/security/deployment:** credentials resolve from configured secret/environment references; no hidden second canonical routing layer; downstream resource needs belong to the selected endpoint.
- **Baseline / recurring cost:** optional; local/self-hosted operation supported and no recurring paid service required.
- **Update:** pinned-version/security/license/API review plus isolated compatibility and full CI.
- **Exit:** remove adapter/dependency/proxy and select another `ModelProvider` without canonical Agent/Task migration.
- **Provenance:** `upstream/litellm.yaml`.

### NousResearch Hermes Agent

- **Purpose:** optional production-oriented orchestration/planning runtime behind platform-owned orchestration and Agent mapping seams.
- **Status:** integrated through #8; v0.21.2 compatibility/reliability validated through #959.
- **Categories:** optional external service; orchestrator adapter.
- **Upstream:** `https://github.com/NousResearch/hermes-agent`; v0.21.2 / tag `v2026.9.11` / `939e45c91d751fadd94dcd1b873ac3cb44846213`.
- **License / review:** MIT; reviewed 2026-09-13.
- **Boundary:** `HermesOrchestrator` implements `Orchestrator`; `HermesAgentMapper` maps canonical Agent/Team definitions. Hermes IDs remain namespaced adapter metadata.
- **Local source / modification:** platform adapter only; no Hermes source vendored.
- **Compatibility/security/deployment:** pinned HTTP JSON run/status/stop/approval/capabilities/health profile; canonical Task/Run/Agent/Plan/Step lifecycle and platform authorization remain platform-owned. External credentials stay secret-backed.
- **Baseline / recurring cost:** optional; no paid service required and self-hosting/local model endpoints are supported.
- **Update:** explicit pinned-revision review plus adapter, real pinned-Hermes and full CI evidence.
- **Exit:** remove adapter/configuration/service and select another `Orchestrator` without canonical lifecycle migration.
- **Provenance:** `upstream/hermes-agent.yaml`.
- **Validation:** `docs/upstream/HERMES_AGENT_V0_21_2_VALIDATION.md`.

### NVIDIA SkillSpector

- **Purpose:** optional static pre-install security evidence for third-party Skills.
- **Status:** integrated through #868 as an advisory provider.
- **Categories:** optional security-evidence adapter/tooling.
- **Upstream:** `https://github.com/NVIDIA/SkillSpector`; `2.11.2` / `69dcdfb74487d361ba4c811d088cfdea2ff3a9dc`.
- **License / review:** Apache-2.0; reviewed 2026-09-12.
- **Boundary:** `SkillSpectorSecurityEvidenceProvider`; scanner output never becomes canonical trust or Approval authority.
- **Local source / modification:** platform-owned adapter/container packaging; exact upstream source may be copied into the production image at build time without modification, with license obligations retained.
- **Compatibility/security/deployment:** only the evaluated `static_no_llm_network_none` profile is supported; container isolation, no network/LLM, read-only staged input and bounded resources are required.
- **Baseline / recurring cost:** optional; no recurring paid service.
- **Update:** explicit pin/image/corpus/security review and full CI/evaluation.
- **Exit:** remove provider/image while retaining immutable historical platform `SecurityEvidence`.
- **Provenance:** `upstream/skillspector.yaml` and `providers/skillspector/provider-lock.json`.

### ScoreSymphony AI-Agent-VPS Forge subsystem — historical

- **Purpose:** historical provenance for the former execution-only Forge compatibility runtime from #9.
- **Status:** **removed under #991** after the #889 and #46 generic/reference removal gates passed.
- **Categories:** removed adapter integration; historical external runtime; reference-only architectural influence.
- **Upstream:** `https://github.com/ScoreSymphony/AI-Agent-VPS`; final reviewed runtime revision `00b821bc94767865457814bf282982ca242a2e10`.
- **License / review:** MIT; reviewed 2026-09-13.
- **Boundary:** no active platform adapter remains. `ForgeExecutor`, `ForgeHttpClient`, the sidecar CI lane and maintained #46 Forge evidence were removed. The platform-owned `Executor` contract and canonical lifecycle remain independent.
- **Local source / modification:** no upstream Forge source is vendored. The former platform-owned `adapters/forge.py` and `adapters/forge_http.py` were deleted under #991.
- **Compatibility/security/deployment:** **no current Forge compatibility or deployment claim exists**. No Forge sidecar is configured, started or required. Historical backend-private fields may remain only in portability rejection/history logic so old state cannot become canonical authority.
- **Baseline / recurring cost:** not required; no current runtime resource or paid-service requirement.
- **Update:** historical record only. Routine Forge upstream changes do not update this repository. Reintroduction would require a new adoption/support/security/conformance decision.
- **Exit:** completed under #991; no canonical Task/Run/Event/Workspace data migration was required because Forge never owned canonical state.
- **ADR / evidence:** `docs/adr/0013-deprecate-forge-execution-adapter-pending-removal-gates.md`, `docs/integrations/FORGE_RETENTION_DECISION.md`, historical reuse/audit/transport records.
- **Provenance:** `upstream/forge-ai-agent-vps.yaml` (`status: removed`).

### PostgreSQL

- **Purpose:** optional self-hosted transactional coordination authority for the multi-instance Control Plane HA reference profile.
- **Status:** approved for #566 and integrated when the corresponding adapter/runtime profile is present.
- **Categories:** optional external self-hosted service; adapter integration.
- **Upstream:** `https://github.com/postgres/postgres`; reviewed target `18.6`.
- **License / review:** PostgreSQL License; reviewed 2026-09-13.
- **Boundary:** platform-owned `CoordinationProvider`; PostgreSQL does not own canonical Task/Run/Agent state.
- **Local source / modification:** no vendored server source.
- **Compatibility/security/deployment:** uses transactional coordination primitives; endpoint must be private/authenticated and DSNs remain secrets.
- **Baseline / recurring cost:** optional for HA; no recurring paid service required.
- **Update:** explicit compatibility/security/license review plus deterministic and real-process integration evidence.
- **Exit:** replace `CoordinationProvider` after safe reconciliation without canonical-domain migration.
- **Provenance:** `upstream/postgresql-ha-coordination.yaml`.

### Psycopg 3

- **Purpose:** optional Python PostgreSQL transport for the HA coordination adapter.
- **Status:** approved/integrated with the #566 PostgreSQL coordination profile.
- **Categories:** optional library dependency; adapter transport.
- **Upstream:** `https://github.com/psycopg/psycopg`; `3.3.5`.
- **License / review:** LGPL-3.0-only; reviewed 2026-09-13.
- **Boundary:** private connection transport; no Psycopg types become canonical contracts.
- **Local source / modification:** package dependency only; no source copied.
- **Compatibility/security/deployment:** Python >=3.12 platform; pure package expects `libpq`; driver/backend errors are translated without leaking DSNs.
- **Baseline / recurring cost:** optional `ha-postgres` extra; no recurring paid service.
- **Update:** explicit dependency/API/license/security review plus PostgreSQL integration/full CI.
- **Exit:** replace private transport while preserving coordination contracts.
- **Provenance:** `upstream/psycopg-ha-coordination.yaml`.

## Current direct build/development dependencies

Packages declared by `pyproject.toml` remain third-party software even when they are not architecture-significant. Architecture-significant entries are additionally governed above.

| Package | Role | Manifest constraint | License | Architecture-significant? |
| --- | --- | --- | --- | --- |
| setuptools | build backend | `>=75` | MIT | no |
| wheel | build | unbounded build-system entry | MIT | no |
| build | development build tool | `>=1.2,<2` | MIT | no |
| jsonschema | capability validation | `==4.26.0` | MIT | yes |
| mcp | optional MCP transport | `==2.1.1` | MIT | yes |
| litellm | optional model gateway | `==1.99.0` | MIT outside excluded enterprise code | yes |
| uvicorn | optional ASGI server | `>=0.35,<1` | BSD-3-Clause | no |
| psycopg | optional PostgreSQL HA transport | `==3.3.5` | LGPL-3.0-only | yes |
| pytest | tests | `>=8.3,<10` | MIT | no |
| ruff | lint/format | `>=0.12,<1` | MIT | no |
| mypy | type checking | `>=1.17,<3` | MIT | no |

## Review expectations

- External services, forks, vendored/selectively copied source and strategically important dependencies require explicit provenance and update review.
- Reference-only historical influence does not imply active integration.
- Adapter integrations identify the platform-owned boundary and retained compatibility evidence.
- Removing an integration removes current support/release/deployment claims but may retain historical provenance when that helps future architecture, licensing or migration review.
- Architecture-significant upstream changes may not silently redefine canonical platform contracts.

Before adopting or reintroducing an upstream, use `docs/UPSTREAM_ADOPTION_CHECKLIST.md`. Validation examples in `docs/UPSTREAM_POLICY_VALIDATION.md` are not themselves integration approvals.
