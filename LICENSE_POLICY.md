# License Policy

This repository is intended to remain MIT-licensed at the project level. Third-party software may only be integrated in ways that preserve that licensing model and keep upstream obligations explicit.

## Integration categories

Every third-party component must be classified before integration:

1. **Vendored or forked source** — upstream source is copied into this repository or maintained as a project-owned fork.
2. **Library dependency** — the component is consumed through the package/dependency system and remains outside this repository's source tree.
3. **External self-hosted service** — the platform communicates with a separately deployed service over a documented interface.
4. **Protocol or specification** — the platform implements or consumes an interoperability standard without importing an upstream runtime.
5. **Optional adapter** — integration code lives in this repository, while the upstream implementation remains replaceable and external.

## Rules for source inclusion

Third-party source must not be copied into this repository until all of the following are recorded:

- canonical upstream repository or source location;
- exact version, tag or commit used;
- upstream license and required notices;
- whether the imported source was modified;
- destination path in this repository;
- responsible adapter/component boundary;
- update and comparison procedure.

Source with terms that cannot coexist with this repository's MIT distribution model must remain an external dependency/service or be rejected. Permissive licenses still retain their own notice and attribution obligations; they are not silently relicensed as MIT.

## Dependency and service rules

Dependencies and external services may retain their own licenses. Their presence does not change the license of project-owned source, but their license, role and deployment requirement must still be documented in `docs/UPSTREAMS.md` before becoming a required production component.

The default platform deployment must not require a recurring paid AI/API service. Optional paid integrations may exist only behind replaceable adapters.

## Provenance

Every integrated component must have a provenance record. A provenance record identifies the upstream, integration category, version/commit, license, local path or interface, modification state, and update policy.

The canonical inventory is `docs/UPSTREAMS.md`. Vendored or forked code must additionally carry upstream notices in or adjacent to its source tree when required by the upstream license.

## Change control

A pull request that adds or materially changes a third-party integration must:

- update `docs/UPSTREAMS.md`;
- identify the integration category;
- record the verified upstream version/commit and license;
- state whether source is copied, modified or only referenced;
- explain why the chosen integration mode is preferable to a looser dependency boundary;
- preserve the platform's replaceable-contract architecture.

If license status is unclear, source inclusion is blocked until it is resolved.

## Upstream updates

Upstream updates are reviewed deliberately rather than pulled directly into production. The update workflow is defined in `docs/UPSTREAM_UPDATE_WORKFLOW.md`.
