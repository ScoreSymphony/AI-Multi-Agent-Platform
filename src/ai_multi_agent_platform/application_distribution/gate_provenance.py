"""Sanitize release-gate runtime provenance before it becomes canonical evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from .evaluation_gate_orchestration import (
    ApplicationReleaseGateCoordinator as _EvaluationApplicationReleaseGateCoordinator,
)
from .models import ApplicationRelease, GateEvidence
from .provenance import sanitize_runtime_provenance


class ApplicationReleaseGateCoordinator(_EvaluationApplicationReleaseGateCoordinator):
    """Release-gate coordinator that keeps projected runtime provenance safe to serialize.

    Canonical release evidence may retain measured worker/executor/runtime metadata, but it must
    not turn artifact-provider metadata into a path or secret exfiltration channel. The underlying
    coordinator remains authoritative for gate orchestration; this final projection boundary only
    sanitizes the optional runtime-provenance view.
    """

    async def reconcile(self, release: ApplicationRelease) -> tuple[GateEvidence, ...]:
        gates = await super().reconcile(release)
        return tuple(_sanitize_gate_runtime_provenance(gate) for gate in gates)


def _sanitize_gate_runtime_provenance(gate: GateEvidence) -> GateEvidence:
    runtime = gate.details.get("runtime_provenance")
    if not isinstance(runtime, Mapping):
        return gate

    details = dict(gate.details)
    details["runtime_provenance"] = sanitize_runtime_provenance(runtime)
    return replace(gate, details=details)
