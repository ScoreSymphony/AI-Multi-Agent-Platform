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

## Current direct build/development dependencies

These packages are third-party software already declared by `pyproject.toml`. Packages promoted to required or architecture-significant production use must also appear in the registry above when required by `LICENSE_POLICY.md`.

| Package | Role | Manifest constraint | Canonical upstream | License reviewed 2026-09-02 | Architecture-significant now? |
| --- | --- | --- | --- | --- | --- |
| setuptools | build backend requirement | `>=75` | `https://github.com/pypa/setuptools` | MIT | no |
| wheel | build requirement | unbounded in build-system manifest | `https://github.com/pypa/wheel` | MIT | no |
| build | development build tool | `>=1.2,<2` | `https://github.com/pypa/build` | MIT | no |
| jsonschema | runtime capability schema validation | `==4.26.0` | `https://github.com/python-jsonschema/jsonschema` | MIT | yes; integrated for #12 |
| mcp | optional MCP transport + CI integration coverage | `==2.1.1` | `https://github.com/modelcontextprotocol/python-sdk` | MIT | yes; optional adapter recorded above |
| pytest | test runner | `>=8.3,<9` | `https://github.com/pytest-dev/pytest` | MIT | no |
| ruff | linting | `>=0.12,<1` | `https://github.com/astral-sh/ruff` | MIT | no |
| mypy | static type checking | `>=1.17,<2` | `https://github.com/python/mypy` | MIT | no |

The manifest currently uses version constraints rather than a repository lockfile for most packages, so exact resolved tool versions are environment-dependent. Architecture-significant #12 dependencies are pinned directly; their transitive packages still resolve from upstream metadata until a repository-wide lock/reproducible-build policy is introduced.

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
