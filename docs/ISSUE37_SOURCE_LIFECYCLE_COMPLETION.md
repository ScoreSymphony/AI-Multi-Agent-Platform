# Issue #37 source lifecycle completion

This follow-up closes the final gap found during the post-merge acceptance audit for workspace/project-environment management.

## Source resolution invariant

Provider-neutral `WorkspaceSourceRef` values are resolved by the canonical Control Plane before the workspace provider creates the initial snapshot. Resolved canonical `WorkspaceFile` entries are merged with any explicitly supplied canonical files and then frozen into that initial immutable `WorkspaceSnapshot`.

This means later materialization reads only the exact snapshot manifest. A repository, artifact, template, or previous-snapshot source is never re-resolved dynamically for each Run, preserving reproducibility.

Canonical `files` source references are provenance for the explicit `files` manifest supplied with workspace creation and therefore require no second provider lookup. Snapshot sources are resolved by the built-in snapshot resolver. Repository/artifact/template source kinds remain explicitly unavailable until a connector registers the corresponding resolver.

## Project isolation

Snapshot source resolution verifies the source Workspace project against the current `DataAccessContext`. A snapshot from another project cannot be attached merely by knowing its canonical snapshot ID. Full policy authorization remains the responsibility of #15, but the workspace domain now enforces its own project-consistency invariant.

## Cleanup reconciliation

If a known local materialization disappears outside the normal release path, `cleanup()` reports it as missing and removes its local materialization bookkeeping. Workspace `active_task_ids` and `active_run_ids` are recomputed only from remaining materializations belonging to the same Workspace, preventing stale references from indefinitely blocking retention decisions.

## Completion coverage

`tests/test_issue37_source_lifecycle_completion.py` verifies:

- snapshot source -> Control Plane -> initial canonical snapshot -> local materialization end to end;
- the derived Workspace remains pinned to the original source snapshot after the source Workspace advances;
- cross-project snapshot attachment is rejected;
- explicit files cannot silently overwrite resolved source paths;
- unregistered repository sources fail explicitly rather than producing an empty Workspace;
- missing known materializations are reconciled with active task/run references.
