"""Outer adapters from Marketplace component kinds to canonical owner domains.

The Marketplace owns metadata and distribution handoff only.  These adapters deliberately keep
runtime/lifecycle authority in Skills, Plugins/Capabilities/Connectors and Applications.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

from jsonschema.exceptions import ValidationError  # type: ignore[import-untyped]

from ai_multi_agent_platform.applications import (
    Application,
    ApplicationInstallRequest,
    ApplicationInstance,
    ApplicationLifecycleService,
    ApplicationManifest,
    ApplicationObservedState,
    ApplicationRepository,
    ApplicationRuntimeRegistry,
    application_manifest_from_document,
)
from ai_multi_agent_platform.agents import AgentService
from ai_multi_agent_platform.agents.models import AgentRevision, AgentTeamRevision
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.distribution import PluginRegistryArtifactInstaller
from ai_multi_agent_platform.distribution.items import RegistryItem
from ai_multi_agent_platform.distribution.models import RegistryItemType
from ai_multi_agent_platform.domain import Provenance
from ai_multi_agent_platform.plugins import ExtensionType, PluginManifest, PluginRegistry
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
from ai_multi_agent_platform.skills.codec import skill_revision_from_json
from ai_multi_agent_platform.skills.models import SkillRevision
from ai_multi_agent_platform.skills.service import SkillService


def _requirements(item: RegistryItem, *, owner_domain: str) -> dict[str, object]:
    return {
        "owner_domain": owner_domain,
        "requested_permissions": tuple(sorted(item.requested_permissions)),
        "required_capabilities": tuple(sorted(item.required_capabilities)),
        "required_plugins": tuple(item.required_plugins),
        "required_connectors": tuple(item.required_connectors),
        "required_models": tuple(item.required_models),
    }


def _json_object(artifact: bytes, *, label: str) -> dict[str, object]:
    try:
        value = json.loads(artifact.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"{label} artifact must be a UTF-8 JSON object",
        ) from exc
    if not isinstance(value, dict):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"{label} artifact must be a JSON object",
        )
    return value


class PluginMarketplaceKindHandler:
    """Expose existing Plugin owner operations without replacing the legacy install route."""

    kind = RegistryItemType.PLUGIN

    def __init__(
        self,
        installer: PluginRegistryArtifactInstaller,
        registry: PluginRegistry,
    ) -> None:
        self._installer = installer
        self._registry = registry

    def inspect_requirements(self, item: RegistryItem) -> Mapping[str, object]:
        return _requirements(item, owner_domain="plugins")

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        return await self._installer.install_verified_plugin(item, artifact)

    async def update(self, item: RegistryItem, artifact: bytes) -> object:
        return await self._installer.install_verified_plugin(item, artifact)

    async def uninstall(self, item: RegistryItem) -> object:
        self._registry.remove(item.item_id)
        return None

    async def status(self, item: RegistryItem) -> object:
        return self._registry.get(item.item_id)

    def describe(self, item: RegistryItem) -> Mapping[str, object]:
        manifest = self._registry.manifest(item.item_id)
        return {
            "owner_domain": "plugins",
            "plugin_id": manifest.plugin_id,
            "plugin_version": manifest.plugin_version,
            "extension_types": tuple(
                sorted({extension.extension_type.value for extension in manifest.extensions})
            ),
        }


class PluginExtensionMarketplaceKindHandler:
    """Install semantic provider packages through the existing Plugin owner.

    The Marketplace kind describes the user-facing role (for example Orchestrator or Model
    Provider), while installation/update/removal stay canonical Plugin lifecycle operations.
    Configuration and activation remain explicit Plugin-owner steps; this adapter never owns
    provider runtime state or credentials.
    """

    def __init__(
        self,
        *,
        kind: RegistryItemType,
        extension_type: ExtensionType,
        installer: PluginRegistryArtifactInstaller,
        registry: PluginRegistry,
    ) -> None:
        self._kind = kind
        self._extension_type = extension_type
        self._installer = installer
        self._registry = registry

    @property
    def kind(self) -> RegistryItemType:
        return self._kind

    def inspect_requirements(self, item: RegistryItem) -> Mapping[str, object]:
        return {
            **_requirements(item, owner_domain="plugins"),
            "required_extension_type": self._extension_type.value,
        }

    def _validated_manifest(self, item: RegistryItem, artifact: bytes) -> PluginManifest:
        manifest = self._installer.validated_manifest(item, artifact)
        if not any(
            extension.extension_type is self._extension_type for extension in manifest.extensions
        ):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                (
                    f"{item.kind} Marketplace artifact must declare at least one "
                    f"{self._extension_type.value} extension"
                ),
            )
        return manifest

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        self._validated_manifest(item, artifact)
        return await self._installer.install_verified_plugin(item, artifact)

    async def update(self, item: RegistryItem, artifact: bytes) -> object:
        self._validated_manifest(item, artifact)
        return await self._installer.install_verified_plugin(item, artifact)

    async def uninstall(self, item: RegistryItem) -> object:
        self._registry.remove(item.item_id)
        return None

    async def status(self, item: RegistryItem) -> object:
        return self._registry.get(item.item_id)

    def describe(self, item: RegistryItem) -> Mapping[str, object]:
        manifest = self._registry.manifest(item.item_id)
        return {
            "owner_domain": "plugins",
            "plugin_id": manifest.plugin_id,
            "plugin_version": manifest.plugin_version,
            "extension_type": self._extension_type.value,
            "extensions": tuple(
                extension.extension_id
                for extension in manifest.extensions
                if extension.extension_type is self._extension_type
            ),
        }



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

    def _current(self, item: RegistryItem) -> AgentRevision:
        current = self._service.get_agent_revision(item.item_id)
        if not self._belongs_to_item(current.provenance, item):
            raise ContractError(
                ErrorCode.CONFLICT,
                "canonical Agent is not owned by this Marketplace installation",
            )
        return current

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        snapshot = self._decode(item, artifact)
        created: AgentRevision | None = None
        try:
            for candidate in snapshot.revisions:
                if candidate.revision == 1:
                    created = self._service.create_agent(
                        candidate.profile,
                        owner_ref=candidate.owner_ref,
                        project_id=candidate.project_id,
                        workspace_id=candidate.workspace_id,
                        provenance=self._provenance(item),
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
                        provenance=self._provenance(item),
                    )
            if created is None:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "Marketplace Agent artifact contains no canonical revisions",
                )
            return created
        except BaseException:
            if created is not None:
                try:
                    self._service.delete_agent(snapshot.definition.agent_id)
                except ContractError:
                    pass
            raise

    async def update(self, item: RegistryItem, artifact: bytes) -> object:
        snapshot = self._decode(item, artifact)
        current = self._current(item)
        candidate = snapshot.revisions[-1]
        if candidate.revision != current.revision + 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Marketplace Agent update must advance the canonical revision by exactly one",
                details={
                    "current_revision": current.revision,
                    "candidate_revision": candidate.revision,
                },
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
            provenance=self._provenance(item),
        )

    async def uninstall(self, item: RegistryItem) -> object:
        current = self._current(item)
        self._service.delete_agent(current.agent_id, expected_owner_ref=current.owner_ref)
        return None

    async def status(self, item: RegistryItem) -> object:
        return self._current(item)

    def describe(self, item: RegistryItem) -> Mapping[str, object]:
        current = self._current(item)
        return {
            "owner_domain": "agents",
            "agent_id": current.agent_id,
            "revision": current.revision,
            "name": current.profile.name,
            "required_capabilities": tuple(
                constraint.capability_id for constraint in current.profile.capabilities.required
            ),
            "optional_capabilities": tuple(
                constraint.capability_id for constraint in current.profile.capabilities.optional
            ),
            "memory_scopes": tuple(
                scope.value for scope in sorted(current.profile.data_access.memory_scopes, key=lambda value: value.value)
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

    def _current(self, item: RegistryItem) -> AgentTeamRevision:
        current = self._service.get_team_revision(item.item_id)
        if not self._belongs_to_item(current.provenance, item):
            raise ContractError(
                ErrorCode.CONFLICT,
                "canonical Agent Team is not owned by this Marketplace installation",
            )
        return current

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        snapshot = self._decode(item, artifact)
        created: AgentTeamRevision | None = None
        try:
            for candidate in snapshot.revisions:
                if candidate.revision == 1:
                    created = self._service.create_team(
                        candidate.profile,
                        owner_ref=candidate.owner_ref,
                        project_id=candidate.project_id,
                        workspace_id=candidate.workspace_id,
                        provenance=self._provenance(item),
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
                        provenance=self._provenance(item),
                    )
            if created is None:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "Marketplace Agent Team artifact contains no canonical revisions",
                )
            return created
        except BaseException:
            if created is not None:
                try:
                    self._service.delete_team(snapshot.definition.team_id)
                except ContractError:
                    pass
            raise

    async def update(self, item: RegistryItem, artifact: bytes) -> object:
        snapshot = self._decode(item, artifact)
        current = self._current(item)
        candidate = snapshot.revisions[-1]
        if candidate.revision != current.revision + 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Marketplace Agent Team update must advance the canonical revision by exactly one",
                details={
                    "current_revision": current.revision,
                    "candidate_revision": candidate.revision,
                },
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
            provenance=self._provenance(item),
        )

    async def uninstall(self, item: RegistryItem) -> object:
        current = self._current(item)
        self._service.delete_team(current.team_id, expected_owner_ref=current.owner_ref)
        return None

    async def status(self, item: RegistryItem) -> object:
        return self._current(item)

    def describe(self, item: RegistryItem) -> Mapping[str, object]:
        current = self._current(item)
        return {
            "owner_domain": "agents",
            "team_id": current.team_id,
            "revision": current.revision,
            "name": current.profile.name,
            "member_count": len(current.profile.members),
            "member_agents": tuple(member.agent.agent_id for member in current.profile.members),
            "shared_resources": tuple(current.profile.shared_resource_refs),
        }


class SkillMarketplaceKindHandler:
    """Delegate Skill releases to the canonical SkillService revision lifecycle."""

    kind = RegistryItemType.SKILL

    def __init__(self, service: SkillService) -> None:
        self._service = service

    def inspect_requirements(self, item: RegistryItem) -> Mapping[str, object]:
        return _requirements(item, owner_domain="skills")

    @staticmethod
    def _decode(item: RegistryItem, artifact: bytes) -> SkillRevision:
        revision = skill_revision_from_json(_json_object(artifact, label="skill"))
        source = revision.profile.source
        if source is None:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Marketplace Skill artifact must declare canonical third-party source metadata",
            )
        if source.license != item.license:
            raise ContractError(
                ErrorCode.CONFLICT,
                "registry license does not match Skill source license",
            )
        return revision

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
            },
        )

    @classmethod
    def _belongs_to_item(cls, revision: SkillRevision, item: RegistryItem) -> bool:
        provenance = revision.provenance
        return (
            provenance is not None
            and provenance.source == "marketplace"
            and provenance.details.get("registry_item_id") == item.item_id
            and provenance.details.get("source_registry") == cls._source_identity(item)
        )

    def _find_current(self, item: RegistryItem) -> SkillRevision:
        matches = tuple(
            revision
            for revision in self._service.list_skills()
            if self._belongs_to_item(revision, item)
        )
        if not matches:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"Marketplace Skill is not installed: {item.item_id}",
            )
        if len(matches) > 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"Marketplace Skill maps to multiple canonical Skills: {item.item_id}",
            )
        return matches[0]

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        revision = self._decode(item, artifact)
        if revision.revision != 1:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "a new Marketplace Skill must provide canonical revision 1",
            )
        return self._service.create_skill(
            revision.profile,
            owner_ref=revision.owner_ref,
            project_id=revision.project_id,
            workspace_id=revision.workspace_id,
            provenance=self._provenance(item),
            skill_id=revision.skill_id,
        )

    async def update(self, item: RegistryItem, artifact: bytes) -> object:
        candidate = self._decode(item, artifact)
        current = self._service.get_skill_revision(candidate.skill_id)
        if not self._belongs_to_item(current, item):
            raise ContractError(
                ErrorCode.CONFLICT,
                "Marketplace Skill update does not target the installed Registry item",
            )
        if candidate.revision != current.revision + 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                "Marketplace Skill update must advance the canonical revision by exactly one",
                details={
                    "current_revision": current.revision,
                    "candidate_revision": candidate.revision,
                },
            )
        if (
            candidate.owner_ref != current.owner_ref
            or candidate.project_id != current.project_id
            or candidate.workspace_id != current.workspace_id
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "Marketplace Skill update cannot change canonical ownership scope",
            )
        return self._service.update_skill(
            candidate.skill_id,
            candidate.profile,
            expected_revision=current.revision,
            owner_ref=current.owner_ref,
            project_id=current.project_id,
            workspace_id=current.workspace_id,
            provenance=self._provenance(item),
        )

    async def uninstall(self, item: RegistryItem) -> object:
        current = self._find_current(item)
        self._service.delete_skill(current.skill_id)
        return None

    async def status(self, item: RegistryItem) -> object:
        return self._find_current(item)

    def describe(self, item: RegistryItem) -> Mapping[str, object]:
        current = self._find_current(item)
        return {
            "owner_domain": "skills",
            "skill_id": current.skill_id,
            "revision": current.revision,
            "name": current.profile.name,
            "enabled": current.profile.enabled,
            "deprecated": current.profile.deprecated,
            "trust_status": current.profile.trust_status.value,
            "evaluation_status": current.profile.evaluation_status.value,
        }


class ApplicationMarketplaceKindHandler:
    """Thin Marketplace adapter over the canonical Application lifecycle owner."""

    kind = RegistryItemType.APPLICATION

    def __init__(
        self,
        lifecycle: ApplicationLifecycleService,
        repository: ApplicationRepository,
        runtimes: ApplicationRuntimeRegistry,
    ) -> None:
        self._lifecycle = lifecycle
        self._repository = repository
        self._runtimes = runtimes

    def inspect_requirements(self, item: RegistryItem) -> Mapping[str, object]:
        return _requirements(item, owner_domain="applications")

    @staticmethod
    def _source_ref(item: RegistryItem) -> str:
        source_identity = item.source_registry or item.source.repository
        return f"marketplace:{source_identity}:{item.item_id}@{item.version}"

    @staticmethod
    def _decode(item: RegistryItem, artifact: bytes) -> ApplicationManifest:
        document = _json_object(artifact, label="application")
        try:
            manifest = application_manifest_from_document(document)
        except (ValidationError, ValueError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "invalid canonical Application manifest",
            ) from exc
        if manifest.version != item.version:
            raise ContractError(
                ErrorCode.CONFLICT,
                "registry item version does not match Application manifest version",
            )
        return manifest

    def _find_instance(self, item: RegistryItem) -> tuple[Application, ApplicationInstance]:
        applications = tuple(
            application
            for application in self._repository.list_applications()
            if application.source_ref == self._source_ref(item)
        )
        if not applications:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"Marketplace Application is not installed: {item.item_id}",
            )
        if len(applications) > 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"Marketplace Application maps to multiple canonical definitions: {item.item_id}",
            )
        application = applications[0]
        instances = tuple(
            instance
            for instance in self._repository.list_instances(
                application_id=application.application_id
            )
            if instance.application_version == application.version
            and instance.observed_state is not ApplicationObservedState.REMOVED
        )
        if not instances:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"Marketplace Application has no canonical instance: {item.item_id}",
            )
        if len(instances) > 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"Marketplace Application maps to multiple canonical instances: {item.item_id}",
            )
        return application, instances[0]

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        manifest = self._decode(item, artifact)
        runtime_id = self._runtimes.select_runtime_id(manifest)
        try:
            request = ApplicationInstallRequest(manifest=manifest)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                (
                    "Marketplace Application install requires explicit configuration, secret or "
                    "volume bindings that are not present in the catalog handoff"
                ),
            ) from exc
        return await self._lifecycle.install(
            request,
            runtime_id=runtime_id,
            source_ref=self._source_ref(item),
        )

    async def update(self, item: RegistryItem, artifact: bytes) -> object:
        del item, artifact
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "Application owner does not expose a Marketplace version-update operation",
        )

    async def uninstall(self, item: RegistryItem) -> object:
        _, instance = self._find_instance(item)
        return await self._lifecycle.remove(instance.instance_id)

    async def status(self, item: RegistryItem) -> object:
        _, instance = self._find_instance(item)
        return await self._lifecycle.status(instance.instance_id)

    def describe(self, item: RegistryItem) -> Mapping[str, object]:
        application, instance = self._find_instance(item)
        return {
            "owner_domain": "applications",
            "application_id": application.application_id,
            "application_version": application.version,
            "runtime_id": application.runtime_id,
            "instance_id": instance.instance_id,
            "name": application.name,
        }


__all__ = [
    "AgentMarketplaceKindHandler",
    "AgentTeamMarketplaceKindHandler",
    "ApplicationMarketplaceKindHandler",
    "PluginExtensionMarketplaceKindHandler",
    "PluginMarketplaceKindHandler",
    "SkillMarketplaceKindHandler",
]
