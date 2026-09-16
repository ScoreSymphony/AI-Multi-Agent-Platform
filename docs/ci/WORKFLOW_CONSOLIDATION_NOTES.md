# Workflow consolidation notes

This change deliberately reduces the permanent GitHub Actions surface without changing platform runtime behavior.

## Retired one-off evaluation workflows

The dedicated #862 object-storage evidence campaigns are retired because #862 is completed and their result is recorded in the issue, PR history, research documentation, tests and evidence harnesses. Their workflows were tied to the historical evaluation branch rather than to the ordinary long-lived regression surface.

The three dedicated #859 Bifrost security/performance/DNS-rebinding workflow files are retired as permanent issue-numbered Actions entries, but the remaining live security responsibility is not dropped. The maintained `compatibility.yml` pull-request lane retains pinned Bifrost compatibility and the isolated DNS-rebinding runtime gate through `scripts/ci/bifrost_dns_rebinding_gate.sh`. The authoritative Bifrost evaluation document remains the source of truth for promotion state; specialized historical #859 evidence stays preserved in source history, tests and research records.

The standalone Pipelock candidate-compatibility workflow is retired as a separate Actions entry, but its live pinned-runtime responsibilities are not dropped. `pipelock.yml` remains the maintained Pipelock validation suite and now directly exercises the exact reviewed Core pin against the platform adapter, MCP stdio, MCP HTTP/WebSocket transports, and retained adversarial integration cases. This preserves candidate compatibility coverage while keeping Pipelock under one durable workflow.

## Consolidated durable workflows

`test-layout.yml` and `forge-removal-guard.yml` were consolidated into `repository-quality.yml`. During the initial workflow consolidation, the historical `forge-sidecar-integration` job ID was temporarily preserved only because branch protection still required that context. The later required-check cleanup retires both that artificial job and its branch-protection requirement; Forge retirement remains enforced by the backend-neutral architecture regression in `tests/architecture/test_forge_removal.py`.

`ha-postgres-coordination.yml` and `ha-postgres-persistence.yml` are consolidated into `ha-postgres.yml` while preserving both job IDs and their independent PostgreSQL service databases.

## Routine execution surface

The repository no longer repeats every expensive or specialized lane on each `main` push. Full platform/MCP/acceptance conformance remains path-scoped on relevant pull requests and is exercised completely by the daily scheduled/manual conformance workflow. Repository test-layout reconciliation remains a pull-request validation rather than a duplicate post-merge run.

CodeQL is the deliberate security exception to that routine-push consolidation. It remains required on pull requests, keeps its scheduled scan, supports manual dispatch, and also runs on pushes to `main` so GitHub code scanning re-analyzes the actual default-branch revision immediately after merge instead of waiting for the next schedule.

The canonical `ci.yml` default-branch surface contains only the three core jobs. LiteLLM, Hermes and Bifrost compatibility live in `compatibility.yml`, where the same job identities and validation responsibilities remain automatic on pull requests without being repeated after merge. Together with the retained benchmark and extended-performance push workflows, this gives an ordinary `main` push a nine-check routine baseline. CodeQL adds two security analysis checks outside that routine budget, for eleven total checks on an ordinary default-branch push.

This keeps the ordinary non-security push surface at the documented routine check-count budget while giving default-branch security state prompt post-merge coverage.

## Historical Actions entries

Deleting YAML files alone does not remove old workflow names from the Actions sidebar. `repository-maintenance.yml` contains the bounded history-cleanup job with an explicit allowlist of workflow paths retired by this consolidation. It deletes only completed runs for those reviewed paths and can be run manually. Keeping that maintenance operation in its own manual-only workflow prevents an otherwise skipped cleanup job from appearing on every ordinary PR or default-branch push.
