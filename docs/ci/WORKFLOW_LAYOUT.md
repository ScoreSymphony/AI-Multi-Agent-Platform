# GitHub Actions workflow layout

The repository keeps durable CI organized by stable responsibility rather than by historical issue number.

## Active workflow groups

- `ci.yml` — canonical code, package, frontend and compatibility gates.
- `codeql.yml` — dedicated CodeQL workflow. Keep this file and its job identities stable because GitHub Advanced Security correlates its configurations by workflow/job identity.
- `governance.yml` — dependency review and repository/issue governance.
- `repository-quality.yml` — test-layout policy, the historical required `forge-sidecar-integration` retirement guard, and explicit orphaned Actions-history cleanup.
- `conformance.yml` — platform/MCP conformance.
- `benchmark-smoke.yml` and `performance-extended-smoke.yml` — durable benchmark and pressure/fault smoke coverage.
- `ha-postgres.yml` — PostgreSQL HA coordination and persistence acceptance.
- `pipelock.yml` — maintained Pipelock validation suite.
- `hermes-v0-21-2-candidate.yml`, `skillspector-evaluation.yml`, and `swe-rex-evaluation.yml` — current upstream-specific evaluation/compatibility lanes that still have an active maintained purpose.
- `release-manifest.yml` — explicit release-manifest generation.

## Policy for temporary evidence workflows

Issue- or PR-specific evidence workflows may be added temporarily when a focused evaluation cannot be represented safely by an existing durable workflow. Before the owning issue is closed, the workflow must be classified as one of:

1. **Promote** — move the durable regression into an existing responsibility-oriented workflow;
2. **Retain** — keep a dedicated workflow only when the external runtime or evidence lane remains an independently maintained compatibility surface;
3. **Retire** — remove one-off evaluation automation after its result and evidence are recorded and ordinary CI retains the relevant baseline regression.

Do not keep completed `issue-<number>-*.yml` workflows merely as historical evidence. Git history, issue/PR records, artifacts, tests, scripts and research documents preserve that provenance without expanding the permanent Actions surface.

## Required-check compatibility

Workflow consolidation must preserve branch-protection check contexts. In particular, `forge-sidecar-integration` remains a required historical context even though executable Forge integration has been removed; `repository-quality.yml` owns the retirement guard until branch protection is intentionally changed.

`codeql.yml` must remain independent unless GitHub Advanced Security configuration identity is deliberately migrated and verified on the default branch.

## Actions history cleanup

Deleting a workflow file does not immediately remove its old runs from the Actions sidebar. `repository-quality.yml` therefore contains an explicit cleanup job that compares completed run paths with the current default-branch workflow directory and deletes only runs for paths that no longer exist.

The cleanup can be invoked manually through `workflow_dispatch`. A merge commit containing `[actions-history-cleanup]` also triggers it on `main` so a consolidation can remove newly orphaned histories after the workflow-file changes are authoritative on the default branch.
