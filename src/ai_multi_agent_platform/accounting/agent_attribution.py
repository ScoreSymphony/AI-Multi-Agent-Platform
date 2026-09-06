"""#33 executed Agent/Team attribution for canonical #76 usage records."""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from ai_multi_agent_platform.agents.models import AgentRunRecord
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import UsageRecord


class AgentRunReader(Protocol):
    """Minimal #33 read boundary needed to preserve executed revision provenance."""

    def list_agent_runs(self, run_id: str | None = None) -> tuple[AgentRunRecord, ...]: ...


class AgentRunUsageAttributor:
    """Enrich usage only when telemetry already identifies the executed Agent scope.

    The attributor never turns a planning assignment, UI selection or repository guess into
    canonical usage identity. It requires the UsageRecord to already carry both `run_id`
    and `agent_id`; Team provenance additionally requires an already supplied `team_id`.
    If more than one canonical AgentRun could match, attribution remains unchanged rather
    than selecting one heuristically.
    """

    def __init__(self, runs: AgentRunReader) -> None:
        self._runs = runs

    def __call__(self, record: UsageRecord) -> UsageRecord:
        scope = record.scope
        if scope.run_id is None or scope.agent_id is None:
            return record

        candidates = tuple(
            item
            for item in self._runs.list_agent_runs(scope.run_id)
            if item.agent.agent_id == scope.agent_id and self._team_matches(item, scope.team_id)
        )
        if len(candidates) != 1:
            return record

        executed = candidates[0]
        provenance: dict[str, JsonValue] = dict(record.provenance)
        provenance["agent_run_id"] = executed.agent_run_id
        provenance["agent_revision"] = executed.agent.revision
        if scope.team_id is not None and executed.team is not None:
            provenance["team_revision"] = executed.team.revision
        if executed.orchestrator_adapter_id is not None:
            provenance["orchestrator_adapter_id"] = executed.orchestrator_adapter_id
        return replace(record, provenance=provenance)

    @staticmethod
    def _team_matches(record: AgentRunRecord, team_id: str | None) -> bool:
        if team_id is None:
            return True
        return record.team is not None and record.team.team_id == team_id
