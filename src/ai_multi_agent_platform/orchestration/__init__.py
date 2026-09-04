"""Platform-owned orchestration implementations and provider-neutral selection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, Orchestrator

from .reference import ReferenceOrchestrator


@dataclass(frozen=True, slots=True)
class OrchestratorSelection:
    """Configuration value selecting one registered orchestrator implementation."""

    orchestrator_id: str

    def __post_init__(self) -> None:
        if not self.orchestrator_id.strip():
            raise ValueError("orchestrator_id must not be blank")


class OrchestratorRegistry:
    """Small provider-neutral registry; adapter choice stays outside canonical Tasks."""

    def __init__(self, orchestrators: Mapping[str, Orchestrator] | None = None) -> None:
        self._orchestrators: dict[str, Orchestrator] = {}
        for orchestrator_id, orchestrator in (orchestrators or {}).items():
            self.register(orchestrator_id, orchestrator)

    def register(self, orchestrator_id: str, orchestrator: Orchestrator) -> None:
        if not orchestrator_id.strip():
            raise ValueError("orchestrator_id must not be blank")
        if orchestrator_id in self._orchestrators:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"orchestrator is already registered: {orchestrator_id}",
            )
        self._orchestrators[orchestrator_id] = orchestrator

    def select(self, selection: OrchestratorSelection) -> Orchestrator:
        try:
            orchestrator = self._orchestrators[selection.orchestrator_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                f"configured orchestrator is not registered: {selection.orchestrator_id}",
            ) from exc
        if not orchestrator.descriptor.available:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"configured orchestrator is unavailable: {selection.orchestrator_id}",
                provider_id=orchestrator.descriptor.provider_id,
            )
        return orchestrator

    @property
    def orchestrator_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._orchestrators))


__all__ = ["OrchestratorRegistry", "OrchestratorSelection", "ReferenceOrchestrator"]
