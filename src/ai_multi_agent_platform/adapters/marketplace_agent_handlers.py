"""Marketplace adapters for canonical Agent and Agent Team definitions."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from ai_multi_agent_platform.agents import AgentService
from ai_multi_agent_platform.agents.models import AgentRevision, AgentTeamRevision
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.distribution.items import RegistryItem
from ai_multi_agent_platform.distribution.models import RegistryItemType
from ai_multi_agent_platform.domain import Provenance
from ai_multi_agent_platform.portability import (
    AGENT_RESOURCE_TYPE,
    AGENT_TEAM_RESOURCE_TYPE,
    AgentPortableCodec,
    AgentPortableSnapshot,
    AgentTeamPortableCodec,
    AgentTeamPortableSnapshot,
    ImportContext,
    PortableResource,
    seal_resource,
)

from .marketplace_handler_support import _json_object, _requirements


class _AgentMarketplaceBase:
    def __init__(self, service: AgentService) -> None:
        self._service = service

    @staticmethod
    def _source_identity(item: RegistryItem) -> str:
        return item.source_registry or item.source.repository

    @classmethod
    def _provenance(cls, item: RegistryItem) -> Provenance:
        return Provenance(
            source="marketplace",
            details={
                "registry_item_id": item.item_id,
                "registry_version": item.version,
                "source_registry": cls._source_identity(item),
                "source_repository": item.source.repository,
                "package_reference": item.source.package_reference,
                "catalog_provenance": item.provenance,
            },
        )

    @classmethod
    def _belongs_to_item(cls, provenance: Provenance | None, item: RegistryItem) -> bool:
        return (
            provenance is not None
            and provenance.source == "marketplace"
            and provenance.details.get("registry_item_id") == item.item_id
            and provenance.details.get("source_registry") == cls._source_identity(item)
        )

    @staticmethod
    def _portable_resource(
        item: RegistryItem,
        artifact: bytes,
        *,
        resource_type: str,
        label: str,
    ) -> PortableResource:
        document = _json_object(artifact, label=label)
        return seal_resource(
            PortableResource(
                resource_type=resource_type,
                resource_id=item.item_id,
                resource_version=item.version,
                payload=cast(dict[str, JsonValue], document),
            )
        )


class AgentMarketplaceKindHandler(_AgentMarketplaceBase):
    """Install reusable Agent definitions through the canonical AgentService only."""

    kind = RegistryItemType.AGENT

    def inspect_requirements(self, item: RegistryItem) -> Mapping[str, object]:
        return _requirements(item, owner_domain="agents")

    def _decode(self, item: RegistryItem, artifact: bytes) -> AgentPortableSnapshot:
        value = AgentPortableCodec().deserialize(
            self._portable_resource(
                item,
                artifact,
                resource_type=AGENT_RESOURCE_TYPE,
                label="agent",
            ),
            ImportContext(),
        )
        if not isinstance(value, AgentPortableSnapshot):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Agent Marketplace codec returned the wrong canonical snapshot type",
            )
        if value.definition.agent_id != item.item_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Marketplace item ID must match the canonical Agent ID",
            )
        return value

    @staticmethod
    def _same_revision(current: AgentRevision, candidate: AgentRevision) -> bool:
        return (
            current.agent_id == candidate.agent_id
            and current.revision == candidate.revision
            and current.profile == candidate.profile
            and current.owner_ref == candidate.owner_ref
            and current.project_id == candidate.project_id
            and current.workspace_id == candidate.workspace_id
        )

    def _assert_history_prefix(
        self,
        snapshot: AgentPortableSnapshot,
        through_revision: int,
        *,
        provenance: Provenance | None = None,
    ) -> None:
        if through_revision > snapshot.definition.current_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Marketplace Agent artifact is older than the canonical Agent",
            )
        for candidate in snapshot.revisions[:through_revision]:
            current = self._service.get_agent_revision(candidate.agent_id, candidate.revision)
            if not self._same_revision(current, candidate):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "Marketplace Agent artifact diverges from immutable canonical revision history",
                    details={"revision": candidate.revision},
                )
            if provenance is not None and current.provenance != provenance:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "canonical Agent revision is not owned by this Marketplace release",
                    details={"revision": candidate.revision},
                )

    def _current(self, item: RegistryItem) -> AgentRevision:
        current = self._service.get_agent_revision(item.item_id)
        if not self._belongs_to_item(current.provenance, item):
            raise ContractError(
                ErrorCode.CONFLICT,
                "canonical Agent is not owned by this Marketplace installation",
            )
        return current

    def validate_candidate(self, item: RegistryItem, artifact: bytes) -> None:
        self._decode(item, artifact)

    def describe_candidate(
        self,
        item: RegistryItem,
        artifact: bytes,
    ) -> Mapping[str, object]:
        snapshot = self._decode(item, artifact)
        return self._describe_revision(snapshot.revisions[-1])

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        snapshot = self._decode(item, artifact)
        provenance = self._provenance(item)
        existing: AgentRevision | None = None
        try:
            existing = self._service.get_agent_revision(snapshot.definition.agent_id)
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise

        if existing is not None:
            if not self._belongs_to_item(existing.provenance, item):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "Marketplace Agent install targets an existing non-Marketplace Agent",
                )
            self._assert_history_prefix(
                snapshot,
                existing.revision,
                provenance=provenance,
            )
            if existing.revision == snapshot.definition.current_revision:
                return existing

        created = existing
        start_revision = 0 if existing is None else existing.revision
        try:
            for candidate in snapshot.revisions[start_revision:]:
                if candidate.revision == 1:
                    created = self._service.create_agent(
                        candidate.profile,
                        owner_ref=candidate.owner_ref,
                        project_id=candidate.project_id,
                        workspace_id=candidate.workspace_id,
                        provenance=provenance,
                        agent_id=snapshot.definition.agent_id,
                    )
                else:
                    created = self._service.update_agent(
                        snapshot.definition.agent_id,
                        candidate.profile,
                        expected_revision=candidate.revision - 1,
                        owner_ref=candidate.owner_ref,
                        project_id=candidate.project_id,
                        workspace_id=candidate.workspace_id,
                        provenance=provenance,
                    )
            if created is None:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "Marketplace Agent artifact contains no canonical revisions",
                )
            return created
        # error-boundary: allow-broad-catch=cleanup rollback partial Marketplace Agent install
        except Exception:
            if existing is None and created is not None:
                try:
                    self._service.delete_agent(snapshot.definition.agent_id)
                except ContractError:
                    pass
            raise

    async def update(self, item: RegistryItem, artifact: bytes) -> object:
        snapshot = self._decode(item, artifact)
        current = self._current(item)
        candidate = snapshot.revisions[-1]
        if candidate.revision < current.revision or candidate.revision > current.revision + 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Marketplace Agent update must advance the canonical revision by exactly one",
                details={
                    "current_revision": current.revision,
                    "candidate_revision": candidate.revision,
                },
            )
        self._assert_history_prefix(snapshot, current.revision)
        provenance = self._provenance(item)
        if candidate.revision == current.revision:
            if self._same_revision(current, candidate) and current.provenance == provenance:
                return current
            raise ContractError(
                ErrorCode.CONFLICT,
                "Marketplace Agent update retry does not match canonical owner state",
            )
        if (
            candidate.owner_ref != current.owner_ref
            or candidate.project_id != current.project_id
            or candidate.workspace_id != current.workspace_id
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "Marketplace Agent update cannot change canonical ownership scope",
            )
        return self._service.update_agent(
            item.item_id,
            candidate.profile,
            expected_revision=current.revision,
            owner_ref=current.owner_ref,
            project_id=current.project_id,
            workspace_id=current.workspace_id,
            provenance=provenance,
        )

    async def uninstall(self, item: RegistryItem) -> object:
        try:
            current = self._current(item)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return None
            raise
        self._service.delete_agent(current.agent_id, expected_owner_ref=current.owner_ref)
        return None

    async def status(self, item: RegistryItem) -> object:
        return self._current(item)

    def describe(self, item: RegistryItem) -> Mapping[str, object]:
        return self._describe_revision(self._current(item))

    @staticmethod
    def _describe_revision(current: AgentRevision) -> Mapping[str, object]:
        return {
            "owner_domain": "agents",
            "agent_id": current.agent_id,
            "revision": current.revision,
            "name": current.profile.name,
            "required_capabilities": current.profile.capabilities.required_ids,
            "optional_capabilities": tuple(
                constraint.capability_id
                for constraint in current.profile.capabilities.constraints
                if not constraint.required
            ),
            "memory_scopes": tuple(
                scope.value
                for scope in sorted(
                    current.profile.data_access.memory_scopes,
                    key=lambda value: value.value,
                )
            ),
        }


class AgentTeamMarketplaceKindHandler(_AgentMarketplaceBase):
    """Install reusable Agent Team definitions through the canonical AgentService."""

    kind = RegistryItemType.AGENT_TEAM

    def inspect_requirements(self, item: RegistryItem) -> Mapping[str, object]:
        return _requirements(item, owner_domain="agents")

    def _decode(self, item: RegistryItem, artifact: bytes) -> AgentTeamPortableSnapshot:
        value = AgentTeamPortableCodec().deserialize(
            self._portable_resource(
                item,
                artifact,
                resource_type=AGENT_TEAM_RESOURCE_TYPE,
                label="agent team",
            ),
            ImportContext(),
        )
        if not isinstance(value, AgentTeamPortableSnapshot):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "Agent Team Marketplace codec returned the wrong canonical snapshot type",
            )
        if value.definition.team_id != item.item_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Marketplace item ID must match the canonical Agent Team ID",
            )
        return value

    @staticmethod
    def _same_revision(
        current: AgentTeamRevision,
        candidate: AgentTeamRevision,
    ) -> bool:
        return (
            current.team_id == candidate.team_id
            and current.revision == candidate.revision
            and current.profile == candidate.profile
            and current.owner_ref == candidate.owner_ref
            and current.project_id == candidate.project_id
            and current.workspace_id == candidate.workspace_id
        )

    def _assert_history_prefix(
        self,
        snapshot: AgentTeamPortableSnapshot,
        through_revision: int,
        *,
        provenance: Provenance | None = None,
    ) -> None:
        if through_revision > snapshot.definition.current_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Marketplace Agent Team artifact is older than the canonical Team",
            )
        for candidate in snapshot.revisions[:through_revision]:
            current = self._service.get_team_revision(candidate.team_id, candidate.revision)
            if not self._same_revision(current, candidate):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    (
                        "Marketplace Agent Team artifact diverges from immutable "
                        "canonical revision history"
                    ),
                    details={"revision": candidate.revision},
                )
            if provenance is not None and current.provenance != provenance:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "canonical Agent Team revision is not owned by this Marketplace release",
                    details={"revision": candidate.revision},
                )

    def _current(self, item: RegistryItem) -> AgentTeamRevision:
        current = self._service.get_team_revision(item.item_id)
        if not self._belongs_to_item(current.provenance, item):
            raise ContractError(
                ErrorCode.CONFLICT,
                "canonical Agent Team is not owned by this Marketplace installation",
            )
        return current

    def validate_candidate(self, item: RegistryItem, artifact: bytes) -> None:
        self._decode(item, artifact)

    def describe_candidate(
        self,
        item: RegistryItem,
        artifact: bytes,
    ) -> Mapping[str, object]:
        snapshot = self._decode(item, artifact)
        return self._describe_revision(snapshot.revisions[-1])

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        snapshot = self._decode(item, artifact)
        provenance = self._provenance(item)
        existing: AgentTeamRevision | None = None
        try:
            existing = self._service.get_team_revision(snapshot.definition.team_id)
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise

        if existing is not None:
            if not self._belongs_to_item(existing.provenance, item):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "Marketplace Agent Team install targets an existing non-Marketplace Team",
                )
            self._assert_history_prefix(
                snapshot,
                existing.revision,
                provenance=provenance,
            )
            if existing.revision == snapshot.definition.current_revision:
                return existing

        created = existing
        start_revision = 0 if existing is None else existing.revision
        try:
            for candidate in snapshot.revisions[start_revision:]:
                if candidate.revision == 1:
                    created = self._service.create_team(
                        candidate.profile,
                        owner_ref=candidate.owner_ref,
                        project_id=candidate.project_id,
                        workspace_id=candidate.workspace_id,
                        provenance=provenance,
                        team_id=snapshot.definition.team_id,
                    )
                else:
                    created = self._service.update_team(
                        snapshot.definition.team_id,
                        candidate.profile,
                        expected_revision=candidate.revision - 1,
                        owner_ref=candidate.owner_ref,
                        project_id=candidate.project_id,
                        workspace_id=candidate.workspace_id,
                        provenance=provenance,
                    )
            if created is None:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "Marketplace Agent Team artifact contains no canonical revisions",
                )
            return created
        # error-boundary: allow-broad-catch=cleanup rollback partial Marketplace Team install
        except Exception:
            if existing is None and created is not None:
                try:
                    self._service.delete_team(snapshot.definition.team_id)
                except ContractError:
                    pass
            raise

    async def update(self, item: RegistryItem, artifact: bytes) -> object:
        snapshot = self._decode(item, artifact)
        current = self._current(item)
        candidate = snapshot.revisions[-1]
        if candidate.revision < current.revision or candidate.revision > current.revision + 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Marketplace Agent Team update must advance the canonical revision by exactly one",
                details={
                    "current_revision": current.revision,
                    "candidate_revision": candidate.revision,
                },
            )
        self._assert_history_prefix(snapshot, current.revision)
        provenance = self._provenance(item)
        if candidate.revision == current.revision:
            if self._same_revision(current, candidate) and current.provenance == provenance:
                return current
            raise ContractError(
                ErrorCode.CONFLICT,
                "Marketplace Agent Team update retry does not match canonical owner state",
            )
        if (
            candidate.owner_ref != current.owner_ref
            or candidate.project_id != current.project_id
            or candidate.workspace_id != current.workspace_id
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "Marketplace Agent Team update cannot change canonical ownership scope",
            )
        return self._service.update_team(
            item.item_id,
            candidate.profile,
            expected_revision=current.revision,
            owner_ref=current.owner_ref,
            project_id=current.project_id,
            workspace_id=current.workspace_id,
            provenance=provenance,
        )

    async def uninstall(self, item: RegistryItem) -> object:
        try:
            current = self._current(item)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return None
            raise
        self._service.delete_team(current.team_id, expected_owner_ref=current.owner_ref)
        return None

    async def status(self, item: RegistryItem) -> object:
        return self._current(item)

    def describe(self, item: RegistryItem) -> Mapping[str, object]:
        return self._describe_revision(self._current(item))

    @staticmethod
    def _describe_revision(current: AgentTeamRevision) -> Mapping[str, object]:
        return {
            "owner_domain": "agents",
            "team_id": current.team_id,
            "revision": current.revision,
            "name": current.profile.name,
            "member_count": len(current.profile.members),
            "member_agents": tuple(member.agent.agent_id for member in current.profile.members),
            "shared_resources": tuple(current.profile.shared_resource_refs),
        }
