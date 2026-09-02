# Contributing

## Development flow

1. Start from the latest `main`.
2. Create a focused branch for one issue or tightly related work package.
3. Keep canonical platform contracts independent from concrete upstream implementations.
4. Add or update tests for behavior changes.
5. Format changed code with `ruff format .`.
6. Run the local validation commands before opening a pull request.
7. Open a pull request that references the relevant numbered issue.
8. Prefer squash merges for focused work packages unless preserving commit history is materially useful.

## Local validation

```bash
python -m pip install -e ".[dev]"
ruff format --check .
ruff check .
mypy
pytest
python -m build
```

These checks are intended to stay aligned with `.github/workflows/ci.yml`.

## Architecture changes

Changes to canonical domain entities, lifecycle semantics, public contracts, adapter boundaries, persistence ownership, security boundaries or distributed-node behavior must be documented in the pull request. Significant changes should add or update an architecture decision record before implementation is treated as stable.

Concrete systems such as orchestrators, execution backends, model gateways, tool protocols, memory systems and storage products must integrate through platform-owned contracts rather than redefine them.

## Third-party components

Before adding or materially changing a third-party component:

- classify it according to `LICENSE_POLICY.md`;
- verify its canonical upstream, exact version/tag/commit and current license;
- update `docs/UPSTREAMS.md` when the component becomes approved or integrated;
- preserve required notices for copied or modified source;
- document why vendoring is necessary when a looser dependency/service boundary would work;
- keep optional paid services replaceable and outside the baseline requirement.

If provenance or license compatibility is unclear, do not copy the source into the repository until the uncertainty is resolved.

## Scope discipline

Do not mix unrelated issue work into the same pull request. Do not introduce a new third-party dependency before its role, license, provenance and replaceability have been reviewed.

## Secrets and configuration

Never commit credentials, tokens, private keys or production configuration. Use `.env.example` for documented variable names and safe example values only.
