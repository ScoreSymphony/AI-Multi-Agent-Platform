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

## Current architecture-significant integrated upstreams

**None.**

The platform is still at the architecture/bootstrap stage. Candidate systems such as orchestrators, execution backends, model gateways, tool protocols, memory systems, and storage products must be added here only after the adoption review and when their implementation work is approved or merged.

## Current direct build/development dependencies

These packages are third-party software already declared by `pyproject.toml`, but they are **not currently architecture-significant platform integrations**. They are recorded here to make the inventory scope explicit and to resolve the distinction between repository tooling and platform upstream integrations.

| Package | Role | Manifest constraint | Canonical upstream | License reviewed 2026-09-02 | Architecture-significant now? |
| --- | --- | --- | --- | --- | --- |
| setuptools | build backend requirement | `>=75` | `https://github.com/pypa/setuptools` | MIT | no |
| wheel | build requirement | unbounded in build-system manifest | `https://github.com/pypa/wheel` | MIT | no |
| build | development build tool | `>=1.2,<2` | `https://github.com/pypa/build` | MIT | no |
| jsonschema | schema-validation development dependency | `>=4.25,<5` | `https://github.com/python-jsonschema/jsonschema` | MIT | no; promotion to required runtime use requires architecture-upstream review |
| pytest | test runner | `>=8.3,<9` | `https://github.com/pytest-dev/pytest` | MIT | no |
| ruff | linting | `>=0.12,<1` | `https://github.com/astral-sh/ruff` | MIT | no |
| mypy | static type checking | `>=1.17,<2` | `https://github.com/python/mypy` | MIT | no |

The manifest currently uses version constraints rather than a repository lockfile, so exact resolved tool versions are environment-dependent. If one of these packages becomes a required production/architecture dependency, its exact production pin/revision, provenance, compatibility constraints, update method, and exit strategy must be promoted into the architecture-significant registry before that change is considered complete.

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
