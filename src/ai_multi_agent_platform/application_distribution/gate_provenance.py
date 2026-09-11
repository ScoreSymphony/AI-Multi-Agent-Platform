"""Sanitize release-gate runtime provenance before it becomes canonical evidence."""

from __future__ import annotations

import re
from dataclasses import replace

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security import REDACTED, redact_sensitive, redact_text

from .evaluation_gate_orchestration import (
    ApplicationReleaseGateCoordinator as _EvaluationApplicationReleaseGateCoordinator,
)
from .models import ApplicationRelease, GateEvidence

_WINDOWS_ABSOLUTE_PATH = re.compile(r"^[A-Za-z]:[\\/]")


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
    if not isinstance(runtime, dict):
        return gate

    sanitized = _sanitize_provenance_value(redact_sensitive(runtime))
    details = dict(gate.details)
    details["runtime_provenance"] = sanitized
    return replace(gate, details=details)


def _sanitize_provenance_value(value: JsonValue) -> JsonValue:
    if isinstance(value, dict):
        return {key: _sanitize_provenance_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_provenance_value(item) for item in value]
    if isinstance(value, str):
        redacted = redact_text(value)
        if _is_host_local_path(redacted):
            return REDACTED
        return redacted
    return value


def _is_host_local_path(value: str) -> bool:
    candidate = value.strip()
    if not candidate:
        return False
    return (
        candidate.startswith(("/", "~/", "\\\\", "//", "file://"))
        or _WINDOWS_ABSOLUTE_PATH.match(candidate) is not None
    )
