# Test suite layout

The Python test suite is organized by **stable test responsibility**, not by GitHub issue number or implementation phase.

## Canonical layout

```text
tests/
├── conftest.py
├── fixtures/
├── unit/
├── contract/
├── integration/
├── e2e/
├── performance/
├── regression/
└── release/
```

Directories are created only when they contain tests. `tests/conftest.py` is reserved for repository-wide fixtures and hooks; narrower fixtures should live in the closest relevant suite directory.

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

Use for durable reproductions of previously observed defects or cross-cutting regressions that do not fit one narrower suite. Historical issue numbers may remain in filenames for traceability, but **do not create per-issue test directories**.

### `release/`

Reserved for release, compatibility inventory and release-manifest verification. This existing suite remains separate because it validates release artifacts and policy rather than one runtime component.

### `fixtures/`

Shared repository-wide test fixtures. Prefer local fixtures next to a suite when they are not reused across categories.

## Pytest markers

The following stable markers are registered in `pyproject.toml`:

- `unit`
- `contract`
- `integration`
- `e2e`
- `performance`
- `regression`
- `release`

Markers are optional metadata; **directory placement is the primary ownership signal**. Add a marker when selecting that test type independently of its path is useful.

Examples:

```bash
pytest tests/unit
pytest tests/contract
pytest tests/integration
pytest -m integration
pytest -m "contract or integration"
```

## Migration policy for historical root tests

The repository accumulated a large flat suite before this layout was formalized. Those tests are migrated in safe cohorts rather than mechanically moved all at once.

Before moving an existing test, check all of the following:

1. repository-root calculations based on `__file__` still resolve correctly;
2. sibling helper imports remain importable;
3. fixture paths remain valid;
4. CI, scripts, conformance gates and documentation do not reference the old exact path;
5. the test's category is clear from behavior, not merely from its historical issue title.

If a move would require changing production semantics, split that work from the layout refactor.

## Naming

Name tests for the behavior they protect. Existing `test_issue_*` filenames may remain during migration for traceability, but new tests should prefer behavioral names unless an issue-specific regression identifier materially improves diagnostics.
