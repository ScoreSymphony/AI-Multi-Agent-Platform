# GitHub Actions workflow layout

The repository keeps durable CI organized by stable responsibility rather than by historical issue number.

## Active workflow groups

- `ci.yml` — canonical code, package, frontend and maintained compatibility checks.
- `codeql.yml` — dedicated CodeQL workflow. Keep this file and its job identities stable because GitHub Advanced Security correlates its configurations by workflow/job identity.
- `governance.yml` — dependency review and repository/issue governance.
- `repository-quality.yml` — pull-request test-layout policy and collection reconciliation.
- `repository-maintenance.yml` — manual repository-maintenance operations such as bounded orphaned Actions-history cleanup.
- `conformance.yml` — path-scoped pull-request conformance plus scheduled/manual platform, MCP and acceptance evidence.
- `benchmark-smoke.yml` and `performance-extended-smoke.yml` — durable benchmark and pressure/fault smoke coverage.
- `ha-postgres.yml` — PostgreSQL HA coordination and persistence acceptance.
- `pipelock.yml` — maintained Pipelock validation suite.
- `hermes-v0-21-2-candidate.yml`, `skillspector-evaluation.yml`, and `swe-rex-evaluation.yml` — current upstream-specific evaluation/compatibility lanes that still have an active maintained purpose.
- `release-manifest.yml` — explicit release-manifest generation.

## Merge-gate policy

A GitHub Actions check appearing on a pull request does not by itself make that check a universal merge gate. The repository distinguishes required merge gates from compatibility and extended validation.

### Core merge gates

These checks are required for every pull request to `main`:

- `test`
- `single-node-install-smoke`
- `frontend`

### Security merge gates

These checks are also required for every pull request to `main`:

- `dependency-review`
- `Analyze (python)`
- `Analyze (javascript-typescript)`

The CodeQL checks stay in the dedicated `codeql.yml` workflow so GitHub Advanced Security keeps the established workflow/job configuration identity. CodeQL runs on pull requests to `main` and on its scheduled scan; the redundant post-merge `push` copy is intentionally omitted because the exact merge candidate has already passed the required CodeQL checks.

### Optional compatibility checks

Compatibility checks validate replaceable adapters and integrations without becoming universal branch-protection requirements. They may still run automatically on ordinary pull requests so failures remain visible, but they are not global merge gates unless a narrower release or integration policy explicitly promotes them for that operation.

Current examples include:

- `litellm-compat`
- `hermes-pinned-compat`
- `bifrost-pinned-compat`
- maintained Pipelock compatibility/evaluation jobs
- upstream-candidate workflows such as Hermes candidate validation

LiteLLM and Hermes therefore remain continuously testable while preserving the platform rule that optional providers/adapters do not define the universal merge gate.

### Extended validation

Performance, HA, large conformance, release-specific and other specialized evidence remains outside the global required-check set unless a release/integration procedure explicitly requires it for the operation being performed.

Examples include:

- extended performance smoke
- PostgreSQL HA validation
- broader platform conformance
- release-manifest and release-compatibility validation
- experimental/upstream evaluation workflows

`conformance.yml` does not run its full five-job acceptance surface again on every ordinary `main` push. Relevant pull requests still trigger the path-scoped MCP/application acceptance jobs, and the complete default-branch conformance/acceptance surface runs on the daily schedule or by manual dispatch.

## Routine check-count budget

The default branch should not accumulate every specialized validation lane on every merge. For an ordinary `main` push, the target is **at most 10 check runs** with the current workflow inventory.

The routine push surface keeps the canonical CI and benchmark smoke lanes that are intentionally configured for `push`, plus any other explicitly maintained push-only workflow. Duplicate post-merge CodeQL, full conformance/acceptance, and repository-layout validation are intentionally excluded from routine `main` pushes because they are already covered before merge and/or by scheduled validation.

For ordinary pull requests that do not touch a specialized path-filtered integration surface, the intended baseline is also compact: core CI, the six required merge-gate identities, optional maintained compatibility checks already housed in `ci.yml`, and one repository-quality validation job. Manual maintenance jobs must not appear as skipped checks on ordinary pull requests.

The check-count budget is an execution-surface policy, not a license to delete coverage. Expensive or specialized coverage should move to path-scoped pull requests, scheduled runs, manual validation, or release-specific workflows rather than disappearing.

## Forge retirement

Forge is no longer an active first-party execution backend and no longer owns a CI compatibility lane. The former `forge-sidecar-integration` context was only a historical branch-protection shim after executable Forge integration was removed.

The shim is retired together with the branch-protection requirement. Forge retirement remains protected by `tests/architecture/test_forge_removal.py`, which fails if removed runtime paths or active Forge CI/runtime markers return. Historical ADR, audit and provenance material may remain for traceability without creating an active compatibility claim.

## Branch-protection source of truth

For `main`, branch protection should require exactly the six core/security contexts listed above. Repository workflows define and execute checks; GitHub branch protection decides which check identities are mandatory for every merge.

Do not preserve obsolete jobs merely to satisfy stale branch-protection contexts. When a compatibility surface is retired or reclassified, update branch protection and repository CI together so no permanently pending or artificial required context remains.

## Policy for temporary evidence workflows

Issue- or PR-specific evidence workflows may be added temporarily when a focused evaluation cannot be represented safely by an existing durable workflow. Before the owning issue is closed, the workflow must be classified as one of:

1. **Promote** — move the durable regression into an existing responsibility-oriented workflow;
2. **Retain** — keep a dedicated workflow only when the external runtime or evidence lane remains an independently maintained compatibility surface;
3. **Retire** — remove one-off evaluation automation after its result and evidence are recorded and ordinary CI retains the relevant baseline regression.

Do not keep completed `issue-<number>-*.yml` workflows merely as historical evidence. Git history, issue/PR records, artifacts, tests, scripts and research documents preserve that provenance without expanding the permanent Actions surface.

## Required-check compatibility

Workflow consolidation must preserve the identities of checks that are still intentionally required. It must not preserve historical compatibility contexts after branch protection has deliberately stopped requiring them.

`codeql.yml` must remain independent unless GitHub Advanced Security configuration identity is deliberately migrated and verified on the default branch.

## Actions history cleanup

Deleting a workflow file does not immediately remove its old runs from the Actions sidebar. `repository-maintenance.yml` contains the bounded cleanup job with an explicit allowlist of workflow paths retired by the workflow consolidation. It deletes only completed runs whose recorded path is in that reviewed retirement set; unrelated historical or active workflow runs are not selected.

The cleanup is deliberately manual-only through `workflow_dispatch`. Keeping maintenance outside `repository-quality.yml` prevents an otherwise skipped maintenance job from appearing as an extra check on every pull request or default-branch push.
