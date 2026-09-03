"""Application service for durable, versioned canonical Agents and Agent Teams."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import cast

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.data.models import MemoryScope
from ai_multi_agent_platform.domain import OwnerRef, Provenance

from .models import (
    AgentDefinition,
    AgentProfile,
    AgentRevision,
    AgentTeamDefinition,
    AgentTeamProfile,
    AgentTeamRevision,
    new_agent_id,
    new_team_id,
)
from .repository import AgentRepository


class _Unspecified:
    pass


_UNSPECIFIED = _Unspecified()


class AgentService:
    def __init__(self, repository: AgentRepository) -> None:
        self.repository = repository

    def create_agent(
        self,
        profile: AgentProfile,
        *,
        owner_ref: OwnerRef,
        project_id: str | None = None,
        workspace_id: str | None = None,
        provenance: Provenance | None = None,
        agent_id: str | None = None,
    ) -> AgentRevision:
        canonical_id = agent_id or new_agent_id()
        now = datetime.now(UTC)
        revision = AgentRevision(
            agent_id=canonical_id,
            revision=1,
            profile=profile,
            owner_ref=owner_ref,
            project_id=project_id,
            workspace_id=workspace_id,
            created_at=now,
            provenance=provenance,
        )
        definition = AgentDefinition(
            agent_id=canonical_id,
            owner_ref=owner_ref,
            current_revision=1,
            project_id=project_id,
            workspace_id=workspace_id,
            created_at=now,
            updated_at=now,
        )
        self.repository.create_agent(definition, revision)
        return revision

    def update_agent(
        self,
        agent_id: str,
        profile: AgentProfile,
        *,
        expected_revision: int | None = None,
        owner_ref: OwnerRef | None = None,
        project_id: str | None | _Unspecified = _UNSPECIFIED,
        workspace_id: str | None | _Unspecified = _UNSPECIFIED,
        provenance: Provenance | None = None,
    ) -> AgentRevision:
        current = self.repository.get_agent(agent_id)
        if expected_revision is not None and expected_revision != current.current_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "agent was updated after the caller's base revision",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": current.current_revision,
                },
            )
        next_revision = current.current_revision + 1
        now = datetime.now(UTC)
        resolved_owner = owner_ref or current.owner_ref
        resolved_project = (
            current.project_id if isinstance(project_id, _Unspecified) else project_id
        )
        resolved_workspace = (
            current.workspace_id if isinstance(workspace_id, _Unspecified) else workspace_id
        )
        revision = AgentRevision(
            agent_id=agent_id,
            revision=next_revision,
            profile=profile,
            owner_ref=resolved_owner,
            project_id=resolved_project,
            workspace_id=resolved_workspace,
            created_at=now,
            provenance=provenance,
        )
        definition = replace(
            current,
            owner_ref=resolved_owner,
            current_revision=next_revision,
            project_id=resolved_project,
            workspace_id=resolved_workspace,
            updated_at=now,
        )
        self.repository.update_agent(definition, revision)
        return revision

    def rollback_agent(
        self,
        agent_id: str,
        target_revision: int,
        *,
        expected_revision: int | None = None,
        provenance: Provenance | None = None,
    ) -> AgentRevision:
        historical = self.repository.get_agent_revision(agent_id, target_revision)
        return self.update_agent(
            agent_id,
            historical.profile,
            expected_revision=expected_revision,
            owner_ref=historical.owner_ref,
            project_id=historical.project_id,
            workspace_id=historical.workspace_id,
            provenance=provenance,
        )

    def clone_agent(
        self,
        source_agent_id: str,
        *,
        revision: int | None = None,
        owner_ref: OwnerRef | None = None,
        project_id: str | None | _Unspecified = _UNSPECIFIED,
        workspace_id: str | None | _Unspecified = _UNSPECIFIED,
        name: str | None = None,
        provenance: Provenance | None = None,
    ) -> AgentRevision:
        source_definition = self.repository.get_agent(source_agent_id)
        source_revision = self.repository.get_agent_revision(
            source_agent_id,
            revision or source_definition.current_revision,
        )
        profile = source_revision.profile
        if name is not None:
            profile = replace(profile, name=name)
        resolved_project = (
            source_revision.project_id if isinstance(project_id, _Unspecified) else project_id
        )
        resolved_workspace = (
            source_revision.workspace_id if isinstance(workspace_id, _Unspecified) else workspace_id
        )
        return self.create_agent(
            profile,
            owner_ref=owner_ref or source_revision.owner_ref,
            project_id=resolved_project,
            workspace_id=resolved_workspace,
            provenance=provenance,
        )

    def delete_agent(
        self,
        agent_id: str,
        *,
        expected_owner_ref: OwnerRef | None = None,
    ) -> None:
        current = self.repository.get_agent(agent_id)
        if expected_owner_ref is not None and current.owner_ref != expected_owner_ref:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "agent deletion owner check failed",
                details={"agent_id": agent_id},
            )
        self.repository.delete_agent(agent_id)

    def get_agent_revision(self, agent_id: str, revision: int | None = None) -> AgentRevision:
        definition = self.repository.get_agent(agent_id)
        return self.repository.get_agent_revision(
            agent_id,
            revision or definition.current_revision,
        )

    def ensure_memory_scope(
        self,
        agent_id: str,
        revision: int,
        scope: MemoryScope,
    ) -> None:
        profile = self.repository.get_agent_revision(agent_id, revision).profile
        if scope not in profile.data_access.memory_scopes:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                f"agent revision is not permitted to access {scope.value} memory",
                details={"agent_id": agent_id, "revision": revision, "scope": scope.value},
            )

    def ensure_memory_config(
        self,
        agent_id: str,
        revision: int,
        config_ref: str,
    ) -> None:
        profile = self.repository.get_agent_revision(agent_id, revision).profile
        if config_ref not in profile.data_access.memory_config_refs:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "agent revision is not permitted to use the memory configuration",
                details={
                    "agent_id": agent_id,
                    "revision": revision,
                    "memory_config_ref": config_ref,
                },
            )

    def ensure_knowledge_source(
        self,
        agent_id: str,
        revision: int,
        source_id: str,
    ) -> None:
        profile = self.repository.get_agent_revision(agent_id, revision).profile
        if source_id not in profile.data_access.knowledge_source_ids:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "agent revision is not permitted to access the knowledge source",
                details={"agent_id": agent_id, "revision": revision, "source_id": source_id},
            )

    def ensure_authorization_profile(
        self,
        agent_id: str,
        revision: int,
        profile_ref: str,
    ) -> None:
        profile = self.repository.get_agent_revision(agent_id, revision).profile
        configured = profile.policy_hooks.authorization_profile_ref
        if configured != profile_ref:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "agent revision is not assigned to the authorization profile",
                details={
                    "agent_id": agent_id,
                    "revision": revision,
                    "authorization_profile_ref": profile_ref,
                },
            )

    def create_team(
        self,
        profile: AgentTeamProfile,
        *,
        owner_ref: OwnerRef,
        project_id: str | None = None,
        workspace_id: str | None = None,
        provenance: Provenance | None = None,
        team_id: str | None = None,
    ) -> AgentTeamRevision:
        self._validate_team_members(profile)
        canonical_id = team_id or new_team_id()
        now = datetime.now(UTC)
        revision = AgentTeamRevision(
            team_id=canonical_id,
            revision=1,
            profile=profile,
            owner_ref=owner_ref,
            project_id=project_id,
            workspace_id=workspace_id,
            created_at=now,
            provenance=provenance,
        )
        definition = AgentTeamDefinition(
            team_id=canonical_id,
            owner_ref=owner_ref,
            current_revision=1,
            project_id=project_id,
            workspace_id=workspace_id,
            created_at=now,
            updated_at=now,
        )
        self.repository.create_team(definition, revision)
        return revision

    def update_team(
        self,
        team_id: str,
        profile: AgentTeamProfile,
        *,
        expected_revision: int | None = None,
        owner_ref: OwnerRef | None = None,
        project_id: str | None | _Unspecified = _UNSPECIFIED,
        workspace_id: str | None | _Unspecified = _UNSPECIFIED,
        provenance: Provenance | None = None,
    ) -> AgentTeamRevision:
        self._validate_team_members(profile)
        current = self.repository.get_team(team_id)
        if expected_revision is not None and expected_revision != current.current_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "agent team was updated after the caller's base revision",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": current.current_revision,
                },
            )
        next_revision = current.current_revision + 1
        now = datetime.now(UTC)
        resolved_owner = owner_ref or current.owner_ref
        resolved_project = (
            current.project_id if isinstance(project_id, _Unspecified) else project_id
        )
        resolved_workspace = (
            current.workspace_id if isinstance(workspace_id, _Unspecified) else workspace_id
        )
        revision = AgentTeamRevision(
            team_id=team_id,
            revision=next_revision,
            profile=profile,
            owner_ref=resolved_owner,
            project_id=resolved_project,
            workspace_id=resolved_workspace,
            created_at=now,
            provenance=provenance,
        )
        definition = replace(
            current,
            owner_ref=resolved_owner,
            current_revision=next_revision,
            project_id=resolved_project,
            workspace_id=resolved_workspace,
            updated_at=now,
        )
        self.repository.update_team(definition, revision)
        return revision

    def rollback_team(
        self,
        team_id: str,
        target_revision: int,
        *,
        expected_revision: int | None = None,
        provenance: Provenance | None = None,
    ) -> AgentTeamRevision:
        historical = self.repository.get_team_revision(team_id, target_revision)
        return self.update_team(
            team_id,
            historical.profile,
            expected_revision=expected_revision,
            owner_ref=historical.owner_ref,
            project_id=historical.project_id,
            workspace_id=historical.workspace_id,
            provenance=provenance,
        )

    def clone_team(
        self,
        source_team_id: str,
        *,
        revision: int | None = None,
        owner_ref: OwnerRef | None = None,
        project_id: str | None | _Unspecified = _UNSPECIFIED,
        workspace_id: str | None | _Unspecified = _UNSPECIFIED,
        name: str | None = None,
        provenance: Provenance | None = None,
    ) -> AgentTeamRevision:
        source_definition = self.repository.get_team(source_team_id)
        source_revision = self.repository.get_team_revision(
            source_team_id,
            revision or source_definition.current_revision,
        )
        profile = source_revision.profile
        if name is not None:
            profile = replace(profile, name=name)
        resolved_project = (
            source_revision.project_id if isinstance(project_id, _Unspecified) else project_id
        )
        resolved_workspace = (
            source_revision.workspace_id if isinstance(workspace_id, _Unspecified) else workspace_id
        )
        return self.create_team(
            profile,
            owner_ref=owner_ref or source_revision.owner_ref,
            project_id=resolved_project,
            workspace_id=resolved_workspace,
            provenance=provenance,
        )

    def delete_team(
        self,
        team_id: str,
        *,
        expected_owner_ref: OwnerRef | None = None,
    ) -> None:
        current = self.repository.get_team(team_id)
        if expected_owner_ref is not None and current.owner_ref != expected_owner_ref:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "Agent Team deletion owner check failed",
                details={"team_id": team_id},
            )
        self.repository.delete_team(team_id)

    def get_team_revision(self, team_id: str, revision: int | None = None) -> AgentTeamRevision:
        definition = self.repository.get_team(team_id)
        return self.repository.get_team_revision(
            team_id,
            revision or definition.current_revision,
        )

    def ensure_team_shared_resource(
        self,
        team_id: str,
        revision: int,
        resource_ref: str,
    ) -> None:
        profile = self.repository.get_team_revision(team_id, revision).profile
        if resource_ref not in profile.shared_resource_refs:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "agent team revision is not permitted to use the shared resource",
                details={
                    "team_id": team_id,
                    "revision": revision,
                    "resource_ref": resource_ref,
                },
            )

    def _validate_team_members(self, profile: AgentTeamProfile) -> None:
        member_ids = {member.agent.agent_id for member in profile.members}
        for member in profile.members:
            self.repository.get_agent_revision(member.agent.agent_id, member.agent.revision)
            unknown_delegates = set(member.can_delegate_to) - member_ids
            if unknown_delegates:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "team delegation target is not a member of the same team revision",
                    details={"unknown_agent_ids": cast(JsonValue, sorted(unknown_delegates))},
                )
