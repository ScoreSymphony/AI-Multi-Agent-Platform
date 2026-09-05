# Forge reuse implementation status

Status: **Completed**

Issue: #9 — Audit and port reusable Forge capabilities behind the execution interface

This status page supersedes the pre-sidecar implementation snapshot. It records
the final status only; the original intermediate assessment remains available in
Git history.

## Final outcome

The platform integrates Forge-derived execution behavior through an optional,
execution-only sidecar. It does not integrate the legacy Forge Task/Project
application or adopt its lifecycle as platform state.

- `ForgeExecutor` implements the canonical `Executor` boundary through the
  platform-owned `ForgeClient` protocol.
- `ForgeHttpClient` targets the `forge-executor-sidecar/v1` transport at the
  pinned upstream revision `00b821bc94767865457814bf282982ca242a2e10`.
- The sidecar provides execution submission, health, status, cancellation and
  logs without requiring Forge Task or Project persistence.
- Canonical Task, Run, Step and correlation identities remain platform-owned;
  Forge identities are retained only as namespaced adapter metadata.
- Canonical lifecycle/event persistence and restart recovery remain owned by
  `PlatformKernel`.
- The integration is optional: reference execution remains usable without
  Forge or Rust runtime dependencies.

## Evidence

- [Forge reuse audit](FORGE_REUSE_AUDIT.md) — reuse classification, mappings,
  rejected legacy assumptions and final acceptance-criteria reconciliation.
- [Forge transport assessment](FORGE_TRANSPORT_ASSESSMENT.md) — final
  execution-only transport decision and the continued rejection of the legacy
  task-launch route.
- [`upstream/forge-ai-agent-vps.yaml`](../upstream/forge-ai-agent-vps.yaml) —
  provenance, pinned revision, compatibility constraints and exit strategy.
- `tests/test_forge_executor.py`, `tests/test_forge_http.py`,
  `tests/test_forge_kernel_regressions.py`, `tests/test_forge_optionality.py`
  and `tests/test_forge_sidecar_integration.py` — contract, transport,
  lifecycle, optionality and real-sidecar integration coverage.

## Deliberately rejected path

`POST /api/v1/tasks/{id}/launch` in the legacy Forge application remains
rejected as an executor transport because it requires Forge-owned Task/Project
lifecycle state. The execution-only sidecar is the supported integration path.

## Exit strategy

Removing the Forge adapter or sidecar does not require canonical-state
migration. Platform Tasks, Runs, Events, Artifacts and Results remain valid and
can continue through the reference executor or another `Executor` adapter.
