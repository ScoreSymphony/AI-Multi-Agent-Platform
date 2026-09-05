"""Rollback-capable Agent/Team mutation handlers for portable imports."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from ai_multi_agent_platform.agents.models import (
    AgentDefinition,
    AgentRevision,
    AgentTeamDefinition,
    AgentTeamRevision,
)
from ai_multi_agent_platform.agents.repository import AgentRepository
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode

from .agent_codecs import (
    AGENT_RESOURCE_TYPE,
    AGENT_TEAM_RESOURCE_TYPE,
    AgentPortableSnapshot,
    AgentTeamPortableSnapshot,
)
from .models import PortableResource
from .registry import ImportContext


class AgentImportMutationHandler:
    resource_type = AGENT_RESOURCE_TYPE

    def __init__(self, repository: AgentRepository) -> None:
        self._repository = repository

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource, context
        snapshot = _require_agent_snapshot(value)
        _require_missing_agent(self._repository, snapshot.definition.agent_id)

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del resource, context
        snapshot = _require_agent_snapshot(value)
        created = False
        try:
            first = snapshot.revisions[0]
            self._repository.create_agent(_agent_definition_at(snapshot, first), first)
            created = True
            for revision in snapshot.revisions[1:]:
                self._repository.update_agent(_agent_definition_at(snapshot, revision), revision)
            return snapshot.definition.agent_id
        except Exception:
            if created:
                try:
                    self._repository.delete_agent(snapshot.definition.agent_id)
                except Exception as rollback_error:
                    raise ContractError(
                        ErrorCode.BACKEND_ERROR,
                        "portable Agent apply failed and its internal compensation also failed",
                        details={"agent_id": snapshot.definition.agent_id},
                    ) from rollback_error
            raise

    async def rollback(
        self,
        resource: PortableResource,
        value: object,
        token: object,
        context: ImportContext,
    ) -> None:
        del resource, value, context
        if not isinstance(token, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "portable Agent rollback token must be the imported Agent ID",
            )
        self._repository.delete_agent(token)


class AgentTeamImportMutationHandler:
    resource_type = AGENT_TEAM_RESOURCE_TYPE

    def __init__(self, repository: AgentRepository) -> None:
        self._repository = repository

    async def preflight(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> None:
        del resource, context
        snapshot = _require_team_snapshot(value)
        _require_missing_team(self._repository, snapshot.definition.team_id)

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        del resource, context
        snapshot = _require_team_snapshot(value)
        _validate_team_member_revisions(self._repository, snapshot)
        created = False
        try:
            first = snapshot.revisions[0]
            self._repository.create_team(_team_definition_at(snapshot, first), first)
            created = True
            for revision in snapshot.revisions[1:]:
                self._repository.update_team(_team_definition_at(snapshot, revision), revision)
            return snapshot.definition.team_id
        except Exception:
            if created:
                try:
                    self._repository.delete_team(snapshot.definition.team_id)
                except Exception as rollback_error:
                    raise ContractError(
                        ErrorCode.BACKEND_ERROR,
                        (
                            "portable Agent Team apply failed and its internal compensation "
                            "also failed"
                        ),
                        details={"team_id": snapshot.definition.team_id},
                    ) from rollback_error
            raise

    async def rollback(
        self,
        resource: PortableResource,
        value: object,
        token: object,
        context: ImportContext,
    ) -> None:
        del resource, value, context
        if not isinstance(token, str):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "portable Agent Team rollback token must be the imported Team ID",
            )
        self._repository.delete_team(token)


def _require_agent_snapshot(value: object) -> AgentPortableSnapshot:
    if not isinstance(value, AgentPortableSnapshot):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "portable Agent mutation handler received the wrong decoded resource type",
        )
    return value


def _require_team_snapshot(value: object) -> AgentTeamPortableSnapshot:
    if not isinstance(value, AgentTeamPortableSnapshot):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "portable Team mutation handler received the wrong decoded resource type",
        )
    return value


def _require_missing_agent(repository: AgentRepository, agent_id: str) -> None:
    try:
        repository.get_agent(agent_id)
    except ContractError as exc:
        if exc.code is ErrorCode.NOT_FOUND:
            return
        raise
    raise ContractError(
        ErrorCode.CONFLICT,
        f"Agent appeared after import preview: {agent_id}",
        details={"agent_id": agent_id},
    )


def _require_missing_team(repository: AgentRepository, team_id: str) -> None:
    try:
        repository.get_team(team_id)
    except ContractError as exc:
        if exc.code is ErrorCode.NOT_FOUND:
            return
        raise
    raise ContractError(
        ErrorCode.CONFLICT,
        f"Agent Team appeared after import preview: {team_id}",
        details={"team_id": team_id},
    )


def _agent_definition_at(
    snapshot: AgentPortableSnapshot,
    revision: AgentRevision,
) -> AgentDefinition:
    if revision.revision == snapshot.definition.current_revision:
        return snapshot.definition
    return replace(
        snapshot.definition,
        current_revision=revision.revision,
        updated_at=_revision_update_time(snapshot.definition.created_at, revision.created_at),
    )


def _team_definition_at(
    snapshot: AgentTeamPortableSnapshot,
    revision: AgentTeamRevision,
) -> AgentTeamDefinition:
    if revision.revision == snapshot.definition.current_revision:
        return snapshot.definition
    return replace(
        snapshot.definition,
        current_revision=revision.revision,
        updated_at=_revision_update_time(snapshot.definition.created_at, revision.created_at),
    )


def _revision_update_time(created_at: datetime, revision_created_at: datetime) -> datetime:
    return max(created_at, revision_created_at)


def _validate_team_member_revisions(
    repository: AgentRepository,
    snapshot: AgentTeamPortableSnapshot,
) -> None:
    checked: set[tuple[str, int]] = set()
    for revision in snapshot.revisions:
        for member in revision.profile.members:
            key = (member.agent.agent_id, member.agent.revision)
            if key in checked:
                continue
            repository.get_agent_revision(*key)
            checked.add(key)
