"""Outer adapters from Marketplace component kinds to canonical owner domains.

The Marketplace owns metadata and distribution handoff only.  These adapters deliberately keep
runtime/lifecycle authority in Skills, Plugins/Capabilities/Connectors and Applications.
"""

from __future__ import annotations

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
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.distribution import PluginRegistryArtifactInstaller
from ai_multi_agent_platform.distribution.items import RegistryItem
from ai_multi_agent_platform.distribution.models import RegistryItemType
from ai_multi_agent_platform.domain import Provenance
from ai_multi_agent_platform.plugins import ExtensionType, PluginManifest, PluginRegistry
from ai_multi_agent_platform.security.redaction import redact_sensitive
from ai_multi_agent_platform.skills.codec import skill_revision_from_json
from ai_multi_agent_platform.skills.models import SkillRevision
from ai_multi_agent_platform.skills.service import SkillService

from .marketplace_agent_handlers import (
    AgentMarketplaceKindHandler as AgentMarketplaceKindHandler,
    AgentTeamMarketplaceKindHandler as AgentTeamMarketplaceKindHandler,
)
from .marketplace_handler_support import _json_object, _requirements


_SECRET_SCHEMA_VALUE_KEYS = frozenset({"default", "const", "example", "examples", "enum"})


def _sensitive_field_name(name: str) -> bool:
    probe: JsonValue = {name: "__marketplace_secret_probe__"}
    return redact_sensitive(probe) != probe


def _contains_materialized_secret_schema_value(
    value: JsonValue,
    *,
    sensitive_context: bool = False,
) -> bool:
    if isinstance(value, dict):
        if sensitive_context:
            for key in _SECRET_SCHEMA_VALUE_KEYS:
                if key in value and value[key] not in (None, "", [], {}):
                    return True

        properties = value.get("properties")
        if isinstance(properties, dict):
            for property_name, property_schema in properties.items():
                if not isinstance(property_name, str):
                    continue
                if _contains_materialized_secret_schema_value(
                    property_schema,
                    sensitive_context=sensitive_context or _sensitive_field_name(property_name),
                ):
                    return True

        for key, nested in value.items():
            if key == "properties":
                continue
            if _contains_materialized_secret_schema_value(
                nested,
                sensitive_context=sensitive_context,
            ):
                return True
        return False

    if isinstance(value, list):
        return any(
            _contains_materialized_secret_schema_value(
                nested,
                sensitive_context=sensitive_context,
            )
            for nested in value
        )
    return False


def _reject_embedded_provider_credentials(manifest: PluginManifest) -> None:
    for extension in manifest.extensions:
        if extension.extension_type is not ExtensionType.MODEL_PROVIDER:
            continue
        metadata = cast(JsonValue, dict(extension.metadata))
        if redact_sensitive(metadata) != metadata:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Model Provider Marketplace packages must not embed plaintext credentials",
            )

    schema = cast(JsonValue, dict(manifest.configuration_schema))
    if _contains_materialized_secret_schema_value(schema):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            (
                "Model Provider Marketplace configuration schema must describe secret fields "
                "without embedding credential values"
            ),
        )


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

    def validate_candidate(self, item: RegistryItem, artifact: bytes) -> None:
        self._installer.validated_manifest(item, artifact)

    def describe_candidate(
        self,
        item: RegistryItem,
        artifact: bytes,
    ) -> Mapping[str, object]:
        return self._describe_manifest(self._installer.validated_manifest(item, artifact))

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        return await self._installer.install_verified_plugin(item, artifact)

    async def update(self, item: RegistryItem, artifact: bytes) -> object:
        return await self._installer.install_verified_plugin(item, artifact)

    async def uninstall(self, item: RegistryItem) -> object:
        try:
            self._registry.remove(item.item_id)
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
        return None

    async def status(self, item: RegistryItem) -> object:
        return self._registry.get(item.item_id)

    def describe(self, item: RegistryItem) -> Mapping[str, object]:
        return self._describe_manifest(self._registry.manifest(item.item_id))

    @staticmethod
    def _describe_manifest(manifest: PluginManifest) -> Mapping[str, object]:
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
        if self._extension_type is ExtensionType.MODEL_PROVIDER:
            _reject_embedded_provider_credentials(manifest)
        return manifest

    def validate_candidate(self, item: RegistryItem, artifact: bytes) -> None:
        self._validated_manifest(item, artifact)

    def describe_candidate(
        self,
        item: RegistryItem,
        artifact: bytes,
    ) -> Mapping[str, object]:
        return self._describe_manifest(self._validated_manifest(item, artifact))

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        self._validated_manifest(item, artifact)
        return await self._installer.install_verified_plugin(item, artifact)

    async def update(self, item: RegistryItem, artifact: bytes) -> object:
        self._validated_manifest(item, artifact)
        return await self._installer.install_verified_plugin(item, artifact)

    async def uninstall(self, item: RegistryItem) -> object:
        try:
            self._registry.remove(item.item_id)
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
        return None

    async def status(self, item: RegistryItem) -> object:
        return self._registry.get(item.item_id)

    def describe(self, item: RegistryItem) -> Mapping[str, object]:
        return self._describe_manifest(self._registry.manifest(item.item_id))

    def _describe_manifest(self, manifest: PluginManifest) -> Mapping[str, object]:
        matching_extensions = tuple(
            extension
            for extension in manifest.extensions
            if extension.extension_type is self._extension_type
        )
        return {
            "owner_domain": "plugins",
            "plugin_id": manifest.plugin_id,
            "plugin_version": manifest.plugin_version,
            "extension_type": self._extension_type.value,
            "extensions": tuple(extension.extension_id for extension in matching_extensions),
            "capabilities": tuple(manifest.capabilities),
            "extension_metadata": {
                extension.extension_id: redact_sensitive(
                    cast(JsonValue, dict(extension.metadata))
                )
                for extension in matching_extensions
            },
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

    def validate_candidate(self, item: RegistryItem, artifact: bytes) -> None:
        self._decode(item, artifact)

    def describe_candidate(
        self,
        item: RegistryItem,
        artifact: bytes,
    ) -> Mapping[str, object]:
        return self._describe_revision(self._decode(item, artifact))

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        revision = self._decode(item, artifact)
        if revision.revision != 1:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "a new Marketplace Skill must provide canonical revision 1",
            )
        provenance = self._provenance(item)
        try:
            current = self._service.get_skill_revision(revision.skill_id)
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
        else:
            if (
                current.revision == revision.revision
                and current.profile == revision.profile
                and current.owner_ref == revision.owner_ref
                and current.project_id == revision.project_id
                and current.workspace_id == revision.workspace_id
                and current.provenance == provenance
            ):
                return current
            raise ContractError(
                ErrorCode.CONFLICT,
                "Marketplace Skill install targets an existing non-identical canonical Skill",
            )
        return self._service.create_skill(
            revision.profile,
            owner_ref=revision.owner_ref,
            project_id=revision.project_id,
            workspace_id=revision.workspace_id,
            provenance=provenance,
            skill_id=revision.skill_id,
        )

    async def update(self, item: RegistryItem, artifact: bytes) -> object:
        candidate = self._decode(item, artifact)
        current = self._service.get_skill_revision(candidate.skill_id)
        provenance = self._provenance(item)
        if (
            current.revision == candidate.revision
            and current.profile == candidate.profile
            and current.owner_ref == candidate.owner_ref
            and current.project_id == candidate.project_id
            and current.workspace_id == candidate.workspace_id
            and current.provenance == provenance
        ):
            return current
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
            provenance=provenance,
        )

    async def uninstall(self, item: RegistryItem) -> object:
        try:
            current = self._find_current(item)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return None
            raise
        self._service.delete_skill(current.skill_id)
        return None

    async def status(self, item: RegistryItem) -> object:
        return self._find_current(item)

    def describe(self, item: RegistryItem) -> Mapping[str, object]:
        return self._describe_revision(self._find_current(item))

    @staticmethod
    def _describe_revision(revision: SkillRevision) -> Mapping[str, object]:
        return {
            "owner_domain": "skills",
            "skill_id": revision.skill_id,
            "revision": revision.revision,
            "name": revision.profile.name,
            "enabled": revision.profile.enabled,
            "deprecated": revision.profile.deprecated,
            "trust_status": revision.profile.trust_status.value,
            "evaluation_status": revision.profile.evaluation_status.value,
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

    def validate_candidate(self, item: RegistryItem, artifact: bytes) -> None:
        self._decode(item, artifact)

    def describe_candidate(
        self,
        item: RegistryItem,
        artifact: bytes,
    ) -> Mapping[str, object]:
        manifest = self._decode(item, artifact)
        return {
            "owner_domain": "applications",
            "application_id": manifest.application_id,
            "application_version": manifest.version,
            "name": manifest.name,
            "service_count": len(manifest.services),
        }

    async def install(self, item: RegistryItem, artifact: bytes) -> object:
        manifest = self._decode(item, artifact)
        try:
            application, existing = self._find_instance(item)
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
        else:
            if application.manifest != manifest:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "Marketplace Application install targets a non-identical canonical manifest",
                )
            return existing
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
        try:
            _, instance = self._find_instance(item)
        except ContractError as exc:
            if exc.code is ErrorCode.NOT_FOUND:
                return None
            raise
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
