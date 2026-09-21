# Forge reuse status

Forge is **not an active first-party execution backend**.

The former Forge execution adapter and sidecar transport were removed after the generic execution,
recovery, cancellation, workspace-isolation and backend-neutral conformance guarantees were proven
outside Forge-specific code.

Current maintained sources of truth:

- [Forge retention and removal decision](FORGE_RETENTION_DECISION.md)
- [Adapter support matrix](../ADAPTER_SUPPORT_MATRIX.md)
- [ADR 0014: remove Forge execution adapter after removal gates](../adr/0014-remove-forge-execution-adapter-after-removal-gates.md)

The detailed implementation chronology, branch/PR handoff state and historical acceptance checklist
from the original Forge integration are retained in
[`docs/history/issues/FORGE_REUSE_STATUS_2026-09-03.md`](../history/issues/FORGE_REUSE_STATUS_2026-09-03.md).
Those historical records are provenance only and do not define current support, deployment or
operator requirements.
