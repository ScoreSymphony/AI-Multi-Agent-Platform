# GitHub Actions workflow layout

The repository keeps durable CI organized by stable responsibility rather than by historical issue number.

## Active workflow groups

- `ci.yml` — canonical backend/package/install/frontend merge-gate checks.
- `compatibility.yml` — automatic pull-request compatibility checks for maintained optional integrations such as LiteLLM, Hermes and Bifrost.
- `codeql.yml` — dedicated CodeQL workflow. Keep this file and its job identities stable because GitHub Advanced Security correlates its configurations by workflow/job identity.
- `governance.yml` — pull-request dependency review only.
- `governance-maintenance.yml` — issue metadata validation and scheduled/manual upstream discovery.
- `repository-quality.yml` — pull-request test-layout policy and collection reconciliation.
- `repository-maintenance.yml` — bounded orphaned Actions-history cleanup; it runs on a weekly schedule, by explicit manual dispatch, and when workflow definitions change on `main`.
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

The CodeQL checks stay in the dedicated `codeql.yml` workflow so GitHub Advanced Security keeps the established workflow/job configuration identity. CodeQL runs on pull requests to `main`, on pushes to `main`, on its scheduled scan, and by manual dispatch. The default-branch push is an intentional security exception to routine post-merge deduplication: it makes GitHub code scanning re-analyze the actual default-branch revision immediately after merge so resolved or newly introduced default-branch alerts do not wait for the next scheduled scan.

### Optional compatibility checks

Compatibility checks validate replaceable adapters and integrations without becoming universal branch-protection requirements. The maintained ordinary-PR compatibility jobs live in `compatibility.yml`, so they remain visible before merge without being repeated on the resulting `main` push.

Current examples include:

- `litellm-compat`
- `hermes-pinned-compat`
- `bifrost-pinned-compat`
- maintained Pipelock compatibility/evaluation jobs
- upstream-candidate workflows such as Hermes candidate validation

LiteLLM, Hermes and Bifrost therefore remain continuously testable on pull requests while preserving the platform rule that optional providers/adapters do not define the universal merge gate.

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

The default branch should not accumulate every specialized validation lane on every merge. For an ordinary `main` push, the target is **at most 10 routine, non-security check runs** with the current workflow inventory.

With the current layout, an ordinary `main` push has a routine baseline of **9 check runs**:

- `ci.yml`: 3 core checks (`test`, `single-node-install-smoke`, `frontend`);
- `benchmark-smoke.yml`: 3 benchmark checks;
- `performance-extended-smoke.yml`: 3 extended performance checks.

CodeQL is tracked separately from that routine budget because it is default-branch security validation rather than duplicate product/conformance validation. Its two language jobs add **2 security checks**, so an ordinary `main` push currently produces **11 total checks: 9 routine + 2 CodeQL security checks**.

The other former duplicate post-merge surfaces remain intentionally absent: full conformance/acceptance contributes 0 routine push checks, repository-quality contributes 0, and optional compatibility contributes 0. Those responsibilities remain covered on pull requests, scheduled runs, manual runs, or specialized path-scoped workflows.

For ordinary pull requests that do not touch a specialized path-filtered integration surface, the intended baseline is **10 check runs**:

- 3 core CI checks;
- 3 optional compatibility checks;
- 2 CodeQL checks;
- `dependency-review`;
- `validate-test-layout`.

Issue validation, upstream discovery and Actions-history cleanup live in workflows that do not subscribe to `pull_request`, so they do not create skipped check entries on ordinary PRs. Manual maintenance jobs must not appear as skipped checks on ordinary pull requests.

The routine check-count budget is an execution-surface policy, not a license to delete coverage. Expensive or specialized product validation should move to path-scoped pull requests, scheduled runs, manual validation, or release-specific workflows rather than disappearing. Security validation that must refresh the default-branch security state may be tracked separately when that exception is explicit and covered by governance tests.

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

Deleting a workflow file does not immediately remove its old runs from the Actions sidebar. `repository-maintenance.yml` therefore compares completed workflow runs with the workflow definitions that still exist in the repository. A run is eligible for cleanup only when its recorded path starts with `.github/workflows/` and that exact path is absent from the current active workflow inventory.

Routine cleanup is deliberately bounded. Weekly runs and workflow-definition changes inspect at most 10 pages of 100 recent completed runs, plus recently deleted workflow registrations. Histories of orphaned workflow IDs discovered there are also read with the same 10-page budget. Repeated maintenance runs therefore make steady progress without paginating the repository's complete Actions history on every execution.

A manual dispatch can set `full_history_scan=true` when older legacy workflow history needs a one-time deep cleanup. That mode may paginate the complete Actions history, but deletion remains capped at 4,000 runs per execution. Active workflow paths and non-repository GitHub-managed workflow surfaces are never selected.

The workflow runs on changes under `.github/workflows/**` on `main`, on a weekly schedule, or by explicit manual dispatch. It remains outside `repository-quality.yml` and does not subscribe to pull requests, so maintenance does not become a merge-gate check.
