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
from ai_multi_agent_platform.models import ModelRoutingProfileRef
from ai_multi_agent_platform.models.routing_profile_assignment_context import (
    require_routing_profile_assignment_access,
)

from .agent_codecs import (
    AGENT_RESOURCE_TYPE,
    AGENT_TEAM_RESOURCE_TYPE,
    AgentPortableSnapshot,
    AgentTeamPortableSnapshot,
)
from .dependencies import parse_resource_dependency
from .model_routing_profile_codecs import MODEL_ROUTING_PROFILE_RESOURCE_TYPE
from .models import DependencyKind, PortableResource
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
        snapshot = _require_agent_snapshot(value)
        _require_missing_agent(self._repository, snapshot.definition.agent_id)
        assignments = _routing_profile_assignments(resource, snapshot, context)
        if assignments:
            # Preflight every authorization prerequisite before package mutation, but do
            # not resolve the profile through the gate yet. A referenced profile may be
            # another resource in this same package and is therefore not guaranteed to
            # exist until its dependency is applied earlier in the import order.
            require_routing_profile_assignment_access()

    async def apply(
        self,
        resource: PortableResource,
        value: object,
        context: ImportContext,
    ) -> object:
        snapshot = _require_agent_snapshot(value)
        await _authorize_routing_profile_assignments(resource, snapshot, context)
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


async def _authorize_routing_profile_assignments(
    resource: PortableResource,
    snapshot: AgentPortableSnapshot,
    context: ImportContext,
) -> None:
    assignments = _routing_profile_assignments(resource, snapshot, context)
    if not assignments:
        return
    access = require_routing_profile_assignment_access()
    for reference, revision in assignments:
        await access.authorize(
            reference,
            owner_ref=revision.owner_ref,
            project_id=revision.project_id,
        )


def _routing_profile_assignments(
    resource: PortableResource,
    snapshot: AgentPortableSnapshot,
    context: ImportContext,
) -> tuple[tuple[ModelRoutingProfileRef, AgentRevision], ...]:
    declared = _declared_routing_profile_dependencies(resource, context)
    assignments: dict[
        tuple[str, str, str, str | None],
        tuple[ModelRoutingProfileRef, AgentRevision],
    ] = {}
    for revision in snapshot.revisions:
        raw_ref = revision.profile.model.routing_profile_ref
        if raw_ref is None:
            continue
        try:
            reference = ModelRoutingProfileRef.parse(raw_ref)
        except ValueError as exc:
            if raw_ref.startswith("model_routing_profile_"):
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "portable Agent routing-profile reference must pin an exact canonical revision",
                    details={"routing_profile_ref": raw_ref},
                ) from exc
            # Preserve pre-#309 compatibility routing keys unchanged.
            continue
        if (reference.profile_id, reference.revision) not in declared:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "portable Agent routing-profile assignment is missing its canonical dependency",
                details={"routing_profile_ref": reference.canonical_ref},
            )
        owner = revision.owner_ref
        key = (reference.canonical_ref, owner.type, owner.id, revision.project_id)
        assignments[key] = (reference, revision)
    return tuple(assignments.values())


def _declared_routing_profile_dependencies(
    resource: PortableResource,
    context: ImportContext,
) -> set[tuple[str, int]]:
    declared: set[tuple[str, int]] = set()
    for dependency in resource.dependencies:
        if dependency.kind is not DependencyKind.RESOURCE:
            continue
        parsed = parse_resource_dependency(dependency)
        if parsed.resource_type != MODEL_ROUTING_PROFILE_RESOURCE_TYPE:
            continue
        constraint = dependency.version_constraint
        if constraint is None or not constraint.startswith("==") or not constraint[2:].isdigit():
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "portable Agent routing-profile dependency must pin an exact revision",
                details={"profile_id": parsed.resource_id},
            )
        target_id = context.remap(MODEL_ROUTING_PROFILE_RESOURCE_TYPE, parsed.resource_id)
        declared.add((target_id, int(constraint[2:])))
    return declared


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
