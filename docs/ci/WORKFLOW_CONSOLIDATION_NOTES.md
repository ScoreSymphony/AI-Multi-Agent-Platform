# Workflow consolidation notes

This change deliberately reduces the permanent GitHub Actions surface without changing platform runtime behavior.

## Retired one-off evaluation workflows

The dedicated #862 object-storage evidence campaigns are retired because #862 is completed and their result is recorded in the issue, PR history, research documentation, tests and evidence harnesses. Their workflows were tied to the historical evaluation branch rather than to the ordinary long-lived regression surface.

The dedicated #859 Bifrost security/performance/DNS-rebinding evaluation workflows are retired after the evaluation completed. The ordinary CI workflow retains the pinned Bifrost compatibility lane; the specialized #859 evidence remains preserved in source history, tests and research records rather than as three permanent issue-numbered Actions entries.

The standalone Pipelock candidate-compatibility workflow is retired after #730 completed. `pipelock.yml` remains the maintained Pipelock validation suite and ordinary CI continues to run the repository test suite.

## Consolidated durable workflows

`test-layout.yml` and `forge-removal-guard.yml` are consolidated into `repository-quality.yml`. The `forge-sidecar-integration` job ID is preserved because it is still a required branch-protection context.

`ha-postgres-coordination.yml` and `ha-postgres-persistence.yml` are consolidated into `ha-postgres.yml` while preserving both job IDs and their independent PostgreSQL service databases.

## Historical Actions entries

Deleting YAML files alone does not remove old workflow names from the Actions sidebar. `repository-quality.yml` includes a bounded orphaned-run cleanup job. It deletes only completed runs whose recorded workflow path no longer exists on the repository default branch. It can be run manually and is also available for the post-merge consolidation cleanup.
