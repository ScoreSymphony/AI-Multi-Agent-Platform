# Test suite layout

The Python test suite is organized by **stable test responsibility**, not by GitHub issue number or implementation phase.

## Canonical layout

Collected runtime/behavior tests normally live below one of the seven canonical suite directories:

```text
tests/
├── conftest.py
├── fixtures/
├── architecture/
├── unit/
├── contract/
├── integration/
├── e2e/
├── performance/
├── regression/
└── release/
```

`tests/conftest.py`, `tests/fixtures/` and non-collected shared helper/case modules may remain at stable common locations when multiple canonical test modules reuse them. Ordinary collected `test_*.py` modules must not live directly under `tests/`.

`tests/architecture/` is an intentionally retained repository-policy suite. It verifies repository-wide architecture, package/documentation boundaries and static ownership constraints rather than one runtime test type. It is collected by the full pytest run and may be selected explicitly by directory, but it is not one of the seven runtime suite markers below. It must not be used as a fallback for tests whose responsibility is really unit, contract, integration, E2E, performance, regression or release.

Within a canonical suite, use stable domain-oriented subdirectories whenever the responsibility has a clear owner (for example `unit/browser/`, `contract/models/`, `integration/control_plane/` or `regression/security/`).

## Placement rules

### `unit/`

Use for isolated tests of one component or a small set of collaborators where external processes, real upstream runtimes and cross-service composition are not required. Prefer domain-oriented subdirectories such as `unit/browser/`, `unit/capabilities/` and `unit/cli/`.

### `contract/`

Use for tests that verify canonical platform contracts, provider interfaces, adapter boundaries, error semantics, schema/interface conformance or implementation replaceability. Contract tests should not become implementation-specific merely to match one backend.

### `integration/`

Use when multiple real platform components or a real optional upstream/runtime are exercised together. Upstream compatibility checks belong under a descriptive integration subdirectory such as `integration/upstreams/`.

### `e2e/`

Use for complete user- or platform-visible flows that cross the major runtime boundaries from an entry point to a terminal outcome.

### `performance/`

Use for benchmark, pressure, scale and latency/throughput regression tests. Keep ordinary functional regressions out of this directory.

### `regression/`

Use for durable reproductions of previously observed defects or cross-cutting regressions that do not fit one narrower suite. Preserve historical issue provenance in docstrings, comments, metadata or Git history rather than making the issue number the test module name. **Do not create per-issue test directories.**

### `release/`

Reserved for release, compatibility inventory and release-manifest verification. This existing suite remains separate because it validates release artifacts and policy rather than one runtime component.

### `fixtures/`

Shared repository-wide test fixtures. Prefer local fixtures next to a suite when they are not reused across categories.

## Pytest markers and selection

The following stable markers are registered in `pyproject.toml`:

- `unit`
- `contract`
- `integration`
- `e2e`
- `performance`
- `regression`
- `release`

`tests/conftest.py` automatically assigns the matching marker from a test's first canonical suite directory. Directory placement is the source of truth for these seven markers.

Examples:

```bash
pytest tests/unit
pytest tests/contract
pytest tests/integration
pytest tests/e2e
pytest tests/performance
pytest tests/regression
pytest tests/release
pytest tests/architecture
pytest -m integration
pytest -m "contract or integration"
```

The `Repository quality` workflow executes collection-only directory and marker selections so path/marker drift is detected in CI.

## Completed #722 migration policy

The historical flat/issue-numbered migration is complete. New work must preserve the final-state invariants rather than relying on migration debt:

1. no ordinary `tests/test_*.py` module may be introduced at the test root;
2. no collected test module may be named primarily after a historical issue or PR number;
3. bare numeric provenance tokens are not valid filename taxonomy; semantic identifiers such as a real upstream version remain valid when they describe the behavior under test;
4. exact test paths in CI, scripts and active documentation must be updated with moves;
5. filesystem-relative imports and fixture paths must be revalidated when depth changes;
6. mixed-responsibility modules must be split instead of assigned to an arbitrary suite;
7. shared root helper/case modules are allowed only as non-collected infrastructure reused by canonical test modules, not as a way to hide ordinary tests at the root.

`scripts/ci/validate_test_layout.py` rejects newly introduced root-level and issue-numbered test modules from pull-request diffs. `.github/workflows/repository-quality.yml` additionally audits the complete repository tree on pull requests and `main` pushes, so legacy-style names cannot survive merely because a file was modified rather than added.

The four existing Hermes `v0_21_1` filenames are explicitly allowed because `v0_21_1` is an upstream semantic version, not historical issue/PR provenance. No issue-number filename exception is currently required.

## Provenance rule

Name tests for the behavior they protect. Historical issue/PR provenance belongs in one or more of:

- module or test docstrings when the history materially explains the regression;
- focused comments next to the behavior whose origin matters;
- test metadata/evidence records where machine-readable provenance is useful;
- Git rename/blame history and the migration inventory in `docs/TEST_MIGRATION_722_FINAL.md`.

Do not encode provenance primarily in a test filename or create per-issue directories. A genuinely exceptional fixture that cannot be represented otherwise requires an explicit documented exception instead of silently bypassing the naming rule.
