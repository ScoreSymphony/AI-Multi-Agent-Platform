# Contributing

## Development flow

1. Start from the latest `main`.
2. Create a focused branch for one issue or tightly related work package.
3. Keep canonical platform contracts independent from concrete upstream implementations.
4. Add or update tests for behavior changes.
5. Run the local validation commands before opening a pull request.
6. Open a pull request that references the relevant numbered issue.
7. Prefer squash merges for focused work packages unless preserving commit history is materially useful.

## Local validation

```bash
python -m pip install -e ".[dev]"
ruff check .
mypy
pytest
python -m build
```

## Architecture changes

Changes to canonical domain entities, lifecycle semantics, public contracts, adapter boundaries, persistence ownership, security boundaries or distributed-node behavior must be documented in the pull request. Significant changes should add or update an architecture decision record before implementation is treated as stable.

Concrete systems such as orchestrators, execution backends, model gateways, tool protocols, memory systems and storage products must integrate through platform-owned contracts rather than redefine them.

## Third-party components

Before adding or materially changing an architecture-significant third-party component:

- classify all applicable integration modes using `LICENSE_POLICY.md`;
- complete `docs/UPSTREAM_ADOPTION_CHECKLIST.md` for a new upstream;
- verify the canonical upstream, exact version/tag/commit/deployed revision and current license;
- review required notices and material transitive/bundled license concerns;
- update `docs/UPSTREAMS.md` when the component becomes approved/integrated or its recorded state changes;
- keep provenance metadata compatible with `upstream/PROVENANCE_TEMPLATE.yaml`;
- preserve required notices and traceable origin information for copied, modified, vendored, forked or selectively ported source;
- document why copying/vendoring/forking/porting is necessary when a looser dependency/service/adapter boundary would work;
- document platform adapter/boundary, compatibility constraints, update method and exit/replacement strategy;
- keep optional paid services replaceable and outside the baseline requirement;
- add an ADR when an upstream change materially changes canonical architecture.

If provenance or license compatibility is unclear, do not copy the source into the repository until the uncertainty is resolved.

Architecture-significant upstream changes must follow `docs/UPSTREAM_UPDATE_WORKFLOW.md`; they may not be silently replaced because a newer release exists.

Standard build/development packages are tracked separately from architecture-significant integrations in `docs/UPSTREAMS.md`, but they remain subject to their package licenses and normal dependency review.

## Coding agents

Coding agents are contributors for policy purposes. When an agent adds, removes, upgrades, vendors, forks, ports, or materially changes a third-party integration, it must update the same provenance, registry, notices, tests, and architecture documentation required of a human contributor.

Agents must not infer that an upstream is safe to copy merely because it is public or open source, and must not silently swap architecture-significant upstreams without explicit review.

## Scope discipline

Do not mix unrelated issue work into the same pull request. Do not introduce a new third-party dependency before its role, license, provenance and replaceability have been reviewed at the level appropriate to its architecture impact.

## Secrets and configuration

Never commit credentials, tokens, private keys or production configuration. Use `.env.example` for documented variable names and safe example values only.
