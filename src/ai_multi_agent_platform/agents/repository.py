"""Persistence boundary and deterministic in-memory repository for canonical Agents."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .models import (
    AgentDefinition,
    AgentRevision,
    AgentRunRecord,
    AgentTeamDefinition,
    AgentTeamRevision,
)


class AgentRepository(Protocol):
    def create_agent(self, definition: AgentDefinition, revision: AgentRevision) -> None: ...

    def update_agent(self, definition: AgentDefinition, revision: AgentRevision) -> None: ...

    def delete_agent(self, agent_id: str) -> None: ...

    def get_agent(self, agent_id: str) -> AgentDefinition: ...

    def list_agents(self) -> tuple[AgentDefinition, ...]: ...

    def get_agent_revision(self, agent_id: str, revision: int) -> AgentRevision: ...

    def list_agent_revisions(self, agent_id: str) -> tuple[AgentRevision, ...]: ...

    def create_team(self, definition: AgentTeamDefinition, revision: AgentTeamRevision) -> None: ...

    def update_team(self, definition: AgentTeamDefinition, revision: AgentTeamRevision) -> None: ...

    def delete_team(self, team_id: str) -> None: ...

    def get_team(self, team_id: str) -> AgentTeamDefinition: ...

    def list_teams(self) -> tuple[AgentTeamDefinition, ...]: ...

    def get_team_revision(self, team_id: str, revision: int) -> AgentTeamRevision: ...

    def list_team_revisions(self, team_id: str) -> tuple[AgentTeamRevision, ...]: ...

    def create_agent_run(self, record: AgentRunRecord) -> None: ...

    def update_agent_run(self, record: AgentRunRecord) -> None: ...

    def get_agent_run(self, agent_run_id: str) -> AgentRunRecord: ...

    def list_agent_runs(self, run_id: str | None = None) -> tuple[AgentRunRecord, ...]: ...


class InMemoryAgentRepository:
    """Reference repository preserving every immutable historical revision."""

    def __init__(self) -> None:
        self._agents: dict[str, AgentDefinition] = {}
        self._agent_revisions: dict[tuple[str, int], AgentRevision] = {}
        self._teams: dict[str, AgentTeamDefinition] = {}
        self._team_revisions: dict[tuple[str, int], AgentTeamRevision] = {}
        self._agent_runs: dict[str, AgentRunRecord] = {}

    def create_agent(self, definition: AgentDefinition, revision: AgentRevision) -> None:
        if definition.agent_id in self._agents:
            raise ContractError(ErrorCode.CONFLICT, f"agent already exists: {definition.agent_id}")
        if definition.current_revision != 1 or revision.revision != 1:
            raise ContractError(ErrorCode.CONFLICT, "new agent must start at revision 1")
        self._validate_agent_pair(definition, revision)
        self._agents[definition.agent_id] = definition
        self._agent_revisions[(revision.agent_id, revision.revision)] = revision

    def update_agent(self, definition: AgentDefinition, revision: AgentRevision) -> None:
        current = self.get_agent(definition.agent_id)
        expected = current.current_revision + 1
        if definition.current_revision != expected or revision.revision != expected:
            raise ContractError(
                ErrorCode.CONFLICT,
                "agent revision must increase exactly by one",
                details={
                    "current_revision": current.current_revision,
                    "new_revision": revision.revision,
                },
            )
        self._validate_agent_pair(definition, revision)
        key = (revision.agent_id, revision.revision)
        if key in self._agent_revisions:
            raise ContractError(ErrorCode.CONFLICT, "agent revision already exists")
        self._agent_revisions[key] = revision
        self._agents[definition.agent_id] = definition

    def delete_agent(self, agent_id: str) -> None:
        self.get_agent(agent_id)
        for team in self._teams.values():
            for revision in self.list_team_revisions(team.team_id):
                if any(member.agent.agent_id == agent_id for member in revision.profile.members):
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "agent cannot be deleted while an Agent Team revision references it",
                        details={"agent_id": agent_id, "team_id": team.team_id},
                    )
        for record in self._agent_runs.values():
            if record.agent.agent_id == agent_id:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "agent cannot be deleted while an Agent run references it",
                    details={"agent_id": agent_id, "agent_run_id": record.agent_run_id},
                )
        del self._agents[agent_id]
        for key in tuple(self._agent_revisions):
            if key[0] == agent_id:
                del self._agent_revisions[key]

    def get_agent(self, agent_id: str) -> AgentDefinition:
        try:
            return self._agents[agent_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"agent not found: {agent_id}") from exc

    def list_agents(self) -> tuple[AgentDefinition, ...]:
        return tuple(self._agents[key] for key in sorted(self._agents))

    def get_agent_revision(self, agent_id: str, revision: int) -> AgentRevision:
        try:
            return self._agent_revisions[(agent_id, revision)]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"agent revision not found: {agent_id}@{revision}",
            ) from exc

    def list_agent_revisions(self, agent_id: str) -> tuple[AgentRevision, ...]:
        self.get_agent(agent_id)
        revisions = [
            revision
            for (current_id, _), revision in self._agent_revisions.items()
            if current_id == agent_id
        ]
        return tuple(sorted(revisions, key=lambda item: item.revision))

    def create_team(self, definition: AgentTeamDefinition, revision: AgentTeamRevision) -> None:
        if definition.team_id in self._teams:
            raise ContractError(
                ErrorCode.CONFLICT, f"agent team already exists: {definition.team_id}"
            )
        if definition.current_revision != 1 or revision.revision != 1:
            raise ContractError(ErrorCode.CONFLICT, "new agent team must start at revision 1")
        self._validate_team_pair(definition, revision)
        self._teams[definition.team_id] = definition
        self._team_revisions[(revision.team_id, revision.revision)] = revision

    def update_team(self, definition: AgentTeamDefinition, revision: AgentTeamRevision) -> None:
        current = self.get_team(definition.team_id)
        expected = current.current_revision + 1
        if definition.current_revision != expected or revision.revision != expected:
            raise ContractError(
                ErrorCode.CONFLICT,
                "agent team revision must increase exactly by one",
                details={
                    "current_revision": current.current_revision,
                    "new_revision": revision.revision,
                },
            )
        self._validate_team_pair(definition, revision)
        key = (revision.team_id, revision.revision)
        if key in self._team_revisions:
            raise ContractError(ErrorCode.CONFLICT, "agent team revision already exists")
        self._team_revisions[key] = revision
        self._teams[definition.team_id] = definition

    def delete_team(self, team_id: str) -> None:
        self.get_team(team_id)
        for record in self._agent_runs.values():
            if record.team is not None and record.team.team_id == team_id:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "Agent Team cannot be deleted while an Agent run references it",
                    details={"team_id": team_id, "agent_run_id": record.agent_run_id},
                )
        del self._teams[team_id]
        for key in tuple(self._team_revisions):
            if key[0] == team_id:
                del self._team_revisions[key]

    def get_team(self, team_id: str) -> AgentTeamDefinition:
        try:
            return self._teams[team_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"agent team not found: {team_id}") from exc

    def list_teams(self) -> tuple[AgentTeamDefinition, ...]:
        return tuple(self._teams[key] for key in sorted(self._teams))

    def get_team_revision(self, team_id: str, revision: int) -> AgentTeamRevision:
        try:
            return self._team_revisions[(team_id, revision)]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"agent team revision not found: {team_id}@{revision}",
            ) from exc

    def list_team_revisions(self, team_id: str) -> tuple[AgentTeamRevision, ...]:
        self.get_team(team_id)
        revisions = [
            revision
            for (current_id, _), revision in self._team_revisions.items()
            if current_id == team_id
        ]
        return tuple(sorted(revisions, key=lambda item: item.revision))

    def create_agent_run(self, record: AgentRunRecord) -> None:
        if record.agent_run_id in self._agent_runs:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"agent run already exists: {record.agent_run_id}",
            )
        self.get_agent_revision(record.agent.agent_id, record.agent.revision)
        if record.team is not None:
            self.get_team_revision(record.team.team_id, record.team.revision)
        self._agent_runs[record.agent_run_id] = record

    def update_agent_run(self, record: AgentRunRecord) -> None:
        if record.agent_run_id not in self._agent_runs:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"agent run not found: {record.agent_run_id}",
            )
        previous = self._agent_runs[record.agent_run_id]
        if (
            record.run_id != previous.run_id
            or record.task_id != previous.task_id
            or record.agent != previous.agent
            or record.team != previous.team
            or record.selected_model_config_id != previous.selected_model_config_id
            or record.selected_provider_id != previous.selected_provider_id
            or record.capability_ids != previous.capability_ids
            or record.capability_versions != previous.capability_versions
            or record.orchestrator_adapter_id != previous.orchestrator_adapter_id
            or record.orchestrator_runtime_ref != previous.orchestrator_runtime_ref
            or record.started_at != previous.started_at
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "agent run execution identity and start-time pins are immutable",
            )
        self._agent_runs[record.agent_run_id] = record

    def get_agent_run(self, agent_run_id: str) -> AgentRunRecord:
        try:
            return self._agent_runs[agent_run_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND, f"agent run not found: {agent_run_id}"
            ) from exc

    def list_agent_runs(self, run_id: str | None = None) -> tuple[AgentRunRecord, ...]:
        records = list(self._agent_runs.values())
        if run_id is not None:
            records = [record for record in records if record.run_id == run_id]
        return tuple(sorted(records, key=lambda item: (item.started_at, item.agent_run_id)))

    @staticmethod
    def _validate_agent_pair(definition: AgentDefinition, revision: AgentRevision) -> None:
        if definition.agent_id != revision.agent_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION, "agent definition/revision ID mismatch"
            )
        if definition.current_revision != revision.revision:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "agent definition does not point at supplied revision",
            )
        if (
            definition.owner_ref != revision.owner_ref
            or definition.project_id != revision.project_id
            or definition.workspace_id != revision.workspace_id
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "agent definition ownership scope must match latest revision snapshot",
            )

    @staticmethod
    def _validate_team_pair(
        definition: AgentTeamDefinition,
        revision: AgentTeamRevision,
    ) -> None:
        if definition.team_id != revision.team_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION, "team definition/revision ID mismatch"
            )
        if definition.current_revision != revision.revision:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "team definition does not point at supplied revision",
            )
        if (
            definition.owner_ref != revision.owner_ref
            or definition.project_id != revision.project_id
            or definition.workspace_id != revision.workspace_id
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "team definition ownership scope must match latest revision snapshot",
            )
