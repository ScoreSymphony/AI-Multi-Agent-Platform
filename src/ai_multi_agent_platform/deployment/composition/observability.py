"""Observability foundation for single-node deployment composition."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.observability import InMemoryExporter, Telemetry


@dataclass(frozen=True, slots=True)
class ObservabilityBundle:
    """Process-local observability authorities shared by composition layers."""

    exporter: InMemoryExporter
    telemetry: Telemetry


def build_observability(exporter: InMemoryExporter | None = None) -> ObservabilityBundle:
    """Build the canonical single-node telemetry foundation."""

    effective_exporter = exporter or InMemoryExporter()
    return ObservabilityBundle(
        exporter=effective_exporter,
        telemetry=Telemetry(effective_exporter),
    )
