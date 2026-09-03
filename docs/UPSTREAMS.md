# Upstream Component Registry

This document is the canonical inventory for **architecture-significant third-party integrations** used by the AI Multi-Agent Platform. It also records the repository's current direct build/development packages separately so that the repository does not incorrectly imply that no third-party software is used.

A component is considered an architecture-significant **integrated upstream** only after its provenance entry is complete and the corresponding implementation is merged. Mention in an issue, roadmap, design discussion, or validation example does not make a component integrated.

## Required fields for architecture-significant upstreams

Each approved or integrated component must record:

- **Name**
- **Purpose**
- **Status**: candidate / approved / integrated / deprecated / removed
- **Integration category/categories**
- **Canonical upstream repository**
- **Pinned version/tag/commit or deployed revision**
- **Verified license**
- **License verification date**
- **Last review date**
- **Platform adapter/boundary**
- **Local source path** when vendored, forked, modified, or selectively ported
- **Source origin/path** when copied or selectively ported
- **Modified locally**: yes/no
- **Required notices / attribution**
- **Known compatibility constraints**
- **Security/deployment/resource constraints** where material
- **Required for baseline**: yes/no
- **Recurring paid service required**: yes/no
- **Update/review method**
- **Exit/replacement strategy**
- **ADR** when the upstream materially shapes canonical architecture

The machine-readable starting format is `upstream/PROVENANCE_TEMPLATE.yaml`.

## Current architecture-significant upstreams

### jsonschema

- **Purpose:** Draft 2020-12 JSON Schema validation for canonical capability input/output contracts.
- **Status:** integrated through #12.
- **Integration category/categories:** library dependency.
- **Canonical upstream repository:** `https://github.com/python-jsonschema/jsonschema`.
- **Pinned version/tag/commit or deployed revision:** `4.26.0`.
- **Verified license:** MIT.
- **License verification date:** 2026-09-02.
- **Last review date:** 2026-09-02.
- **Platform adapter/boundary:** internal validation implementation in `ai_multi_agent_platform.capabilities.invocation`; no upstream types are canonical API types.
- **Local source path:** none; normal package dependency only.
- **Source origin/path:** no upstream source copied into this repository.
- **Modified locally:** no.
- **Required notices / attribution:** package retains its upstream license metadata; no vendored source/NOTICE material is redistributed by this repository.
- **Known compatibility constraints:** Python >=3.12 platform baseline; direct package supports Python >=3.10. Full transitive resolution is not repository-locked yet.
- **Security/deployment/resource constraints:** in-process schema validation only; no network service, credentials, GPU, ports or external infrastructure.
- **Required for baseline:** yes, because canonical tool invocation validates declared schemas before execution.
- **Recurring paid service required:** no.
- **Update/review method:** explicit dependency update PR; review upstream release/security notes; run full CI plus capability validation/contract tests.
- **Exit/replacement strategy:** replace the internal validator with another Draft 2020-12-compatible implementation while preserving platform-owned `CapabilitySpec` and invocation contracts.
- **ADR:** none required for the library choice; canonical architecture remains implementation-neutral.
- **Adoption review:** `docs/upstream/JSONSCHEMA_ADOPTION.md`.

### Model Context Protocol Python SDK

- **Purpose:** optional concrete MCP transport for stdio subprocess and Streamable HTTP tool providers.
- **Status:** approved for integration in the #12 completion PR; integrated once that PR is merged.
- **Integration category/categories:** optional adapter/library dependency.
- **Canonical upstream repository:** `https://github.com/modelcontextprotocol/python-sdk`.
- **Pinned version/tag/commit or deployed revision:** `2.1.1`.
- **Verified license:** MIT for the SDK; material dependency set reviewed as permissive (MIT/BSD-3-Clause/Apache-2.0/PSF-2.0 families).
- **License verification date:** 2026-09-02.
- **Last review date:** 2026-09-02.
- **Platform adapter/boundary:** `ai_multi_agent_platform.adapters.mcp_sdk`, implementing the platform-owned `MCPClient` protocol from `ai_multi_agent_platform.adapters.mcp`.
- **Local source path:** none; normal optional package dependency only.
- **Source origin/path:** no upstream source copied into this repository.
- **Modified locally:** no.
- **Required notices / attribution:** installed packages retain their own upstream license metadata; no SDK source/NOTICE material is vendored or redistributed by this repository.
- **Known compatibility constraints:** platform Python >=3.12; SDK Python >=3.10; SDK v2.1.1 is the explicit compatibility target. Streamable HTTP replaces new SSE usage; stdio requires a runnable local command.
- **Security/deployment/resource constraints:** endpoint transport performs configured network access; stdio transport starts a configured process and may receive environment values. Secrets are not copied into canonical adapter metadata or default persistent invocation audit events.
- **Required for baseline:** no; base/native capability operation and core imports must work without the MCP extra.
- **Recurring paid service required:** no.
- **Update/review method:** explicit pinned-version update; review official release/security/licensing changes; run real stdio integration, core optionality architecture test and full repository CI.
- **Exit/replacement strategy:** remove/replace `adapters.mcp_sdk` and satisfy the small platform-owned `MCPClient` protocol with another transport implementation; no agent/task or persisted-data migration is required.
- **ADR:** none required for the SDK choice; the canonical architecture explicitly treats MCP as an optional adapter.
- **Adoption review:** `docs/upstream/MCP_PYTHON_SDK_ADOPTION.md`.

### LiteLLM

- **Purpose:** optional model-gateway compatibility layer for in-process model calls or a separately deployed OpenAI-compatible proxy, behind the platform-owned `ModelProvider` and `ModelRouter` boundaries.
- **Status:** approved for integration in #11; integrated once the corresponding PR is merged.
- **Integration category/categories:** optional adapter/library dependency; optional external service.
- **Canonical upstream repository:** `https://github.com/BerriAI/litellm`.
- **Pinned version/tag/commit or deployed revision:** `v1.99.0` / `fa647f742d7baefe8eb1181899d9c81b41559772`.
- **Verified license:** MIT for content outside `enterprise/`; `enterprise/` is separately licensed and is not used or copied by this integration.
- **License verification date:** 2026-09-03.
- **Last review date:** 2026-09-03.
- **Platform adapter/boundary:** `ai_multi_agent_platform.adapters.litellm.LiteLLMModelProvider` implements the canonical `ModelProvider`; proxy mode reuses the existing OpenAI-compatible provider transport. The platform `ModelRouter` remains authoritative for canonical routing policy.
- **Local source path:** `src/ai_multi_agent_platform/adapters/litellm.py` contains platform-owned adapter code only.
- **Source origin/path:** no LiteLLM source is copied; library mode uses the pinned PyPI dependency and proxy mode targets a separately deployed service.
- **Modified locally:** no upstream source is vendored or modified.
- **Required notices / attribution:** installed packages retain upstream license metadata. Do not copy or vendor `enterprise/` under the MIT assumption; any future source redistribution requires a new license/notices review.
- **Known compatibility constraints:** platform Python >=3.12; LiteLLM `1.99.0` is the explicit SDK compatibility target. Canonical model IDs remain platform-owned and map to LiteLLM/native model strings only inside adapter configuration. Baseline library mode uses direct `acompletion` and intentionally does not enable a second hidden routing/fallback layer.
- **Security/deployment/resource constraints:** credential values are resolved from environment-variable references and are not exposed through canonical metadata. Proxy authentication does not replace platform authentication/authorization. No LiteLLM telemetry callbacks are enabled by the baseline adapter. Resource and GPU requirements depend on the selected downstream endpoint, not on platform core.
- **Required for baseline:** no; core imports, model contracts, reference routing and baseline tests must work without the package or proxy installed.
- **Recurring paid service required:** no; local/self-hosted endpoints are explicitly supported and covered by configuration/tests.
- **Update/review method:** explicit pinned-version update PR; verify the tag/commit and license boundary, review release/security notes and SDK/proxy compatibility, then run baseline CI without LiteLLM, the isolated LiteLLM compatibility job, adapter contract tests and the real local OpenAI-compatible HTTP fixture.
- **Exit/replacement strategy:** remove the optional dependency, adapter/configuration and any separately deployed proxy. Canonical Agents, Tasks, model configuration IDs and `ModelRouter` policy remain valid and can target another `ModelProvider` implementation.
- **ADR:** none required; LiteLLM is deliberately subordinate to the canonical model architecture and is not allowed to redefine routing ownership.
- **Provenance:** `upstream/litellm.yaml`.
- **Adoption/mapping review:** `docs/LITELLM_ADAPTER.md`.

### ScoreSymphony AI-Agent-VPS Forge subsystem

- **Purpose:** source/reference for proven executor, workspace, event/idempotency and recovery behavior reused for issue #9 without importing the legacy Forge lifecycle as platform architecture.
- **Status:** approved; the platform-owned adapter boundary and regression coverage are merged through PR #129, but no concrete legacy Forge runtime transport is integrated yet.
- **Integration category/categories:** adapter integration; reference-only influence.
- **Canonical upstream repository:** `https://github.com/ScoreSymphony/AI-Agent-VPS`.
- **Pinned version/tag/commit or deployed revision:** `5a9f317e3bab056a4cebe214b03912a9b7ad3824`.
- **Verified license:** MIT.
- **License verification date:** 2026-09-03.
- **Last review date:** 2026-09-03.
- **Platform adapter/boundary:** `ai_multi_agent_platform.adapters.forge.ForgeExecutor` behind the canonical `Executor`, with a small platform-owned `ForgeClient` protocol. `ExecutorLifecycleBackend` carries namespaced backend metadata into canonical kernel history.
- **Local source path:** `src/ai_multi_agent_platform/adapters/forge.py` contains new platform-owned adapter code; no upstream Forge source has been copied.
- **Source origin/path:** behavior/specification review of `core/forge/`, especially `crates/executors`, `services/src/domain_event_service.rs`, `services/src/recovery.rs`, task-dispatch/recovery code, workspace code and the public API routes.
- **Modified locally:** no upstream source is presently vendored or modified in this repository.
- **Required notices / attribution:** no copied-source notice is required for the current implementation. If substantial Forge source is selectively copied later, preserve the upstream MIT copyright/license notice and add file-level provenance before merge.
- **Known compatibility constraints:** legacy Forge is primarily Rust and owns its own Task/Project/Execution persistence. Those types and lifecycle rules are not canonical. The current legacy HTTP launch path requires a Forge Task and is intentionally rejected as the first concrete executor transport; see `docs/FORGE_TRANSPORT_ASSESSMENT.md`.
- **Security/deployment/resource constraints:** Forge remains optional. Workspace traversal and artifact boundaries are enforced by the platform adapter; any future process/shell runtime requires explicit policy, environment filtering, sandbox/resource controls and backend authentication without replacing platform authorization.
- **Required for baseline:** no; core startup and reference execution remain Forge-independent.
- **Recurring paid service required:** no.
- **Update/review method:** compare the pinned source revision before changing the adapter/reuse decisions, review license/security/behavior changes, update `docs/FORGE_REUSE_AUDIT.md`, `docs/FORGE_TRANSPORT_ASSESSMENT.md` and `upstream/forge-ai-agent-vps.yaml`, then run executor contract, Forge regression/integration and full repository CI.
- **Exit/replacement strategy:** remove the Forge adapter/transport and adapter-private state. Canonical Task/Run/Event/Workspace state requires no migration because it remains platform-owned.
- **ADR:** none required yet; the architecture deliberately keeps Forge subordinate to existing canonical contracts. A new optional Rust sidecar or other runtime component that materially changes deployment/build topology should receive explicit architecture review.
- **Provenance:** `upstream/forge-ai-agent-vps.yaml`.

## Current direct build/development dependencies

These packages are third-party software already declared by `pyproject.toml`. Packages promoted to required or architecture-significant production use must also appear in the registry above when required by `LICENSE_POLICY.md`.

| Package | Role | Manifest constraint | Canonical upstream | License reviewed 2026-09-02 | Architecture-significant now? |
| --- | --- | --- | --- | --- | --- |
| setuptools | build backend requirement | `>=75` | `https://github.com/pypa/setuptools` | MIT | no |
| wheel | build requirement | unbounded in build-system manifest | `https://github.com/pypa/wheel` | MIT | no |
| build | development build tool | `>=1.2,<2` | `https://github.com/pypa/build` | MIT | no |
| jsonschema | runtime capability schema validation | `==4.26.0` | `https://github.com/python-jsonschema/jsonschema` | MIT | yes; integrated for #12 |
| mcp | optional MCP transport + CI integration coverage | `==2.1.1` | `https://github.com/modelcontextprotocol/python-sdk` | MIT | yes; optional adapter recorded above |
| litellm | optional model gateway SDK / proxy compatibility target | `==1.99.0` | `https://github.com/BerriAI/litellm` | MIT outside `enterprise/`; `enterprise/` separately licensed | yes; optional adapter recorded above |
| pytest | test runner | `>=8.3,<9` | `https://github.com/pytest-dev/pytest` | MIT | no |
| ruff | linting | `>=0.12,<1` | `https://github.com/astral-sh/ruff` | MIT | no |
| mypy | static type checking | `>=1.17,<2` | `https://github.com/python/mypy` | MIT | no |

The manifest currently uses version constraints rather than a repository lockfile for most packages, so exact resolved tool versions are environment-dependent. Architecture-significant #11/#12 dependencies are pinned directly; their transitive packages still resolve from upstream metadata until a repository-wide lock/reproducible-build policy is introduced.

## Architecture-upstream entry template

```text
### <Component name>
- Purpose:
- Status: candidate | approved | integrated | deprecated | removed
- Integration category/categories:
- Canonical upstream repository:
- Pinned version/tag/commit or deployed revision:
- Verified license:
- License verification date:
- Last review date:
- Platform adapter/boundary:
- Local source path:
- Source origin/path:
- Modified locally: yes/no
- Required notices / attribution:
- Known compatibility constraints:
- Security/deployment/resource constraints:
- Required for baseline: yes/no
- Recurring paid service required: yes/no
- Update/review method:
- Exit/replacement strategy:
- ADR:
- Notes:
```

## Status semantics

- **Candidate**: under consideration; not approved for implementation.
- **Approved**: provenance, compatibility, architecture, and licensing reviewed; implementation may proceed.
- **Integrated**: implementation merged and inventory/provenance complete.
- **Deprecated**: still present but scheduled for replacement/removal.
- **Removed**: no longer used by the platform; retained as historical provenance when useful.

## Review expectations by integration type

- External services, forks, vendored source, selective ports, and strategically important dependencies must define an active review/update method.
- Reference-only influences do not require source synchronization, but architecture-significant influence belongs in an ADR or design record.
- Adapter integrations must identify the platform-owned boundary and relevant contract tests.
- Architecture-significant upstream changes must use explicit review and may not silently redefine canonical platform contracts.

## Adoption and validation

Before an upstream becomes approved, use `docs/UPSTREAM_ADOPTION_CHECKLIST.md`.

Policy behavior is exercised by `docs/UPSTREAM_POLICY_VALIDATION.md` using multiple integration models. Those examples are not registry entries and do not constitute integration approval.