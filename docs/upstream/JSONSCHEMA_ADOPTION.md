# jsonschema Runtime Adoption Review

- **Project name:** jsonschema
- **Canonical upstream repository:** https://github.com/python-jsonschema/jsonschema
- **Candidate version:** 4.26.0
- **Integration category:** library dependency
- **Proposed platform boundary:** `ai_multi_agent_platform.capabilities.invocation`
- **Reviewer:** ScoreSymphony
- **Review date:** 2026-09-02
- **Related issue:** #12

## Decision

**Approved** for required runtime use by the canonical capability invocation layer.

The platform needs standards-based, versioned JSON Schema validation before provider execution and, when declared, after provider execution. Implementing a partial validator internally would create security and compatibility risk while duplicating a mature validation library. `jsonschema` remains an implementation dependency behind platform-owned capability contracts; no `jsonschema` object becomes part of the canonical public API.

## Functional fit

- [x] Required capability: validate JSON-compatible capability inputs and outputs against declared Draft 2020-12 schemas.
- [x] `jsonschema` provides Draft 2020-12 validators and schema validation.
- [x] Platform-specific normalization and canonical error mapping remain platform-owned.

## Architecture fit

- [x] The dependency remains behind the capability invocation implementation.
- [x] Canonical Task, Run, Agent, Tool Invocation, Event, Node and Worker identities remain platform-owned.
- [x] Lifecycle, persistence, security and distributed execution ownership are unchanged.
- [x] A normal library dependency is the least-coupled integration mode required.
- [x] Removing/replacing the library does not change `CapabilitySpec`, provider or agent contracts as long as equivalent JSON Schema semantics are preserved.

## Replaceability and exit

Replacement is limited to the internal schema-validation implementation. Candidate replacements include another standards-conformant JSON Schema validator or a platform-owned validator if maintaining one later becomes justified.

No persisted data migration is required to replace the Python library because stored capability schemas remain JSON Schema documents owned by the platform. Rollback is a dependency/code rollback to the prior validated platform revision.

## License and provenance

- [x] Canonical upstream verified: `python-jsonschema/jsonschema`.
- [x] Version 4.26.0 is pinned in `pyproject.toml`.
- [x] Upstream package license verified as MIT on 2026-09-02.
- [x] No upstream source is copied, vendored, forked or selectively ported into this repository.
- [x] Runtime dependency metadata for 4.26.x identifies `attrs`, `jsonschema-specifications`, `referencing` and `rpds-py` as direct dependencies.
- [x] The reviewed direct/transitive projects are permissively licensed (MIT) in the currently published metadata reviewed on 2026-09-02.
- [x] No additional source NOTICE file is redistributed by this repository; installed distributions retain their own package metadata/licenses.

## Project health and maintenance

- [x] Version 4.26.0 was released on 2026-01-07 and is the current PyPI release at review time.
- [x] The upstream project describes itself as production/stable in package metadata.
- [x] Upstream publishes a security policy and states that the latest released version is the supported version.
- [x] Updates can be reviewed as explicit dependency PRs with capability contract/regression tests.

## Security implications

Schema validation runs on platform-controlled schema documents and JSON-compatible values before provider invocation. It does not itself require network access, credentials, filesystem write access, process execution, model access or remote-code loading.

Supply-chain risk is limited to the normal Python runtime dependency chain. The production version is therefore pinned directly and dependency updates must pass the repository CI and capability contract suite.

## Resource footprint

- [x] CPU/memory use is appropriate for per-tool schema validation.
- [x] No GPU requirement.
- [x] No service, port, volume, queue or database requirement.
- [x] No recurring paid service.

## Dependency footprint

The direct runtime dependency is `jsonschema==4.26.0`. Upstream metadata currently declares:

- `attrs>=22.2.0`;
- `jsonschema-specifications>=2023.03.6`;
- `referencing>=0.28.4`;
- `rpds-py>=0.25.0`.

The repository does not yet maintain a full dependency lockfile, so exact transitive resolution remains environment-dependent within upstream compatibility metadata. This is an existing repository-level packaging limitation rather than a capability-contract requirement; a future lock/reproducible-build issue may tighten it without changing #12 contracts.

## API and contract stability

The platform uses only the validation boundary (`Draft202012Validator.check_schema` and validator execution). Exceptions are translated to canonical `ContractError` categories and must not escape as public platform semantics.

Capability tests cover validation-before-execution and output contract violations so an incompatible upstream update is detectable without changing canonical contracts.

## Simpler internal alternative

A platform-owned subset validator was considered and rejected for this stage. JSON Schema Draft 2020-12 semantics are sufficiently broad that a small custom implementation would either be incomplete or become a significant maintenance/security responsibility. The external dependency therefore provides enough value to justify its small runtime footprint.

## Required follow-up before merge

- [x] Pin the direct production dependency.
- [x] Record the upstream in `docs/UPSTREAMS.md`.
- [x] Add capability validation tests.
- [x] Keep the dependency behind platform-owned contracts.
- [ ] Confirm full repository CI is green on the final PR head.

No ADR is required solely for this library adoption because it does not change canonical architecture or public contracts; the canonical decision remains that capabilities use versioned structured schemas independent from the validation implementation.
