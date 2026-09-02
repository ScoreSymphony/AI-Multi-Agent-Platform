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

## Scope discipline

Do not mix unrelated issue work into the same pull request. Do not introduce a new third-party dependency before its role, license, provenance and replaceability have been reviewed.

## Secrets and configuration

Never commit credentials, tokens, private keys or production configuration. Use `.env.example` for documented variable names and safe example values only.
