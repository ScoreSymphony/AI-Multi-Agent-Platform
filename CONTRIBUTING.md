# Contributing

## Development flow

1. Start from the latest `main`.
2. Select an issue with exactly one `type:*`, `area:*`, `stage:*` label and one milestone.
3. Confirm that its hard dependencies are merged before implementation starts.
4. Create a focused branch for one issue or tightly related work package.
5. Keep canonical platform contracts independent from concrete upstream implementations.
6. Add or update tests for behavior changes.
7. Format changed code with `ruff format .`.
8. Run the local validation commands before opening a pull request.
9. Open a pull request that references the relevant numbered issue.
10. Use the repository's squash-merge strategy; the pull-request title becomes the permanent main history entry.

Repository decision-making, ownership, triage and release responsibilities are defined in [`GOVERNANCE.md`](GOVERNANCE.md). The release checklist is maintained in [`docs/RELEASE_PROCESS.md`](docs/RELEASE_PROCESS.md).

## Reconciling stale or superseded branches

When a branch has diverged because equivalent or newer work landed through another pull request, do not resolve conflicts by restoring stale file versions.

1. Compare the branch against the current `main` and identify which changes are still unique.
2. Preserve current canonical `main` behavior for work that has already landed or been superseded.
3. Reapply only still-required changes on top of the current base.
4. Document when a pull request becomes history-only or a reconciliation change rather than pretending old implementation changes are still new.
5. Never use conflict resolution to reintroduce superseded architecture, security fixes, tests or provider-specific assumptions.

A reconciliation commit may preserve branch ancestry, but the resulting tree must be reviewed against current architecture and CI rather than accepted merely because Git reports the conflict as resolved.

Pull requests follow the lifecycle in [GOVERNANCE.md](GOVERNANCE.md): inactive drafts receive a status request after 14 days, work may be marked stale after 30 days, and abandoned work may be closed after 45 days. Security fixes, release candidates and explicitly dependency-blocked work are exempt while their status remains documented. Superseded pull requests must link their replacement and account for any unique remaining diff before closure.

## Requesting work or support

Use the structured GitHub Work item form for actionable repository work. Its Type, Area and Target milestone selections are synchronized to repository metadata by the issue-governance workflow. Use [SUPPORT.md](SUPPORT.md) for support boundaries and [SECURITY.md](SECURITY.md) for private vulnerability reporting.

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

## Canonical runtime assets

Runtime resources that must also live inside the installed Python package have one editable source of truth:

| Canonical source | Generated package copy |
| --- | --- |
| `schemas/backup-manifest-v1.schema.json` | `src/ai_multi_agent_platform/backup/backup-manifest-v1.schema.json` |
| `release/compatibility.json` | `src/ai_multi_agent_platform/release/compatibility.json` |

Edit only the canonical source, then refresh the package copies with:

```bash
python scripts/ci/issue725_materialize_runtime_assets.py
```

Before committing, verify that no generated copy drifted:

```bash
python scripts/ci/issue725_materialize_runtime_assets.py --check
```

CI rejects stale or missing generated copies, materializes the canonical assets immediately before the package build, and verifies that both the wheel and source distribution contain the canonical bytes.

## Architecture changes

Changes to canonical domain entities, lifecycle semantics, public contracts, adapter boundaries, persistence ownership, security boundaries or distributed-node behavior must be documented in the pull request. Significant changes should add or update an architecture decision record before implementation is treated as stable.

Concrete systems such as orchestrators, execution backends, model gateways, tool protocols, memory systems and storage products must integrate through platform-owned contracts rather than redefine them.

## Python package boundaries

The checked top-level package inventory is [`docs/PACKAGE_BOUNDARIES.toml`](docs/PACKAGE_BOUNDARIES.toml); its design rules and migration policy are in [`docs/PACKAGE_BOUNDARIES.md`](docs/PACKAGE_BOUNDARIES.md).

A pull request that adds a package directly below `src/ai_multi_agent_platform/` must update the inventory in the same change and explain why the functionality cannot live as a module or subpackage under an existing owner. The rationale must identify a durable responsibility, state/contract ownership and dependency direction. A feature name, issue number, endpoint, provider or implementation class is not by itself a root-package boundary.

When changing package layout:

- preserve supported import/API behavior or provide a bounded compatibility namespace and migration plan;
- add canonical behavior under the package identified as `owner`, not under a package marked `migration`;
- keep `operations`, `integration`, `surface` and `quality` packages from becoming accidental canonical lifecycle authorities;
- use nested modules/subpackages for responsibility splits such as #723 unless ownership itself is intentionally changing;
- update architecture documentation or an ADR when the move changes canonical ownership rather than only source layout;
- run the architecture tests, affected domain tests, import/package-discovery checks and the normal full validation suite.

The architecture test intentionally fails when the actual root package set and the inventory diverge. Do not bypass that guard by registering a new root package without a meaningful ownership rationale.

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
