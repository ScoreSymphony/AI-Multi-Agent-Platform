"""Canonical owner-domain handoff for validated registry artifacts."""

from __future__ import annotations

import json
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .items import RegistryItem


class PluginArtifactInstaller(Protocol):
    """Deployment-owned #20 bridge for a verified plugin artifact.

    The distribution package deliberately does not deserialize plugin manifests or load
    runtimes. A deployment must compose that work through the canonical plugin owner.
    """

    async def install_verified_plugin(self, item: RegistryItem, artifact: bytes) -> object: ...


class PortablePackageInspection(Protocol):
    @property
    def package_id(self) -> str: ...


class PortableImportPreview(Protocol):
    @property
    def preview_id(self) -> str: ...

    @property
    def ready(self) -> bool: ...


class PortabilityImportOwner(Protocol):
    """Structural subset of #79's PortabilityWorkflowService used by the handoff."""

    def validate_package_document(self, document: object) -> PortablePackageInspection: ...

    def preview_import(self, package_id: str) -> PortableImportPreview: ...

    async def execute_import(self, preview_id: str) -> object: ...


class CanonicalDistributionRouter:
    """Route validated Registry artifacts into their canonical owner domains."""

    def __init__(
        self,
        *,
        plugin_installer: PluginArtifactInstaller | None = None,
        portability: PortabilityImportOwner | None = None,
    ) -> None:
        self._plugin_installer = plugin_installer
        self._portability = portability

    async def install_plugin(self, item: RegistryItem, artifact: bytes) -> object:
        if self._plugin_installer is None:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "registry plugin activation requires a canonical #20 artifact installer",
            )
        return await self._plugin_installer.install_verified_plugin(item, artifact)

    async def import_portable(self, item: RegistryItem, artifact: bytes) -> object:
        del item
        if self._portability is None:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "registry portable activation requires the canonical #79 import workflow",
            )
        try:
            document = json.loads(artifact.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "registry portable artifact is not a valid UTF-8 JSON package",
            ) from exc

        inspection = self._portability.validate_package_document(document)
        preview = self._portability.preview_import(inspection.package_id)
        if not preview.ready:
            raise ContractError(
                ErrorCode.CONFLICT,
                "registry portable artifact is not ready for canonical import",
            )
        return await self._portability.execute_import(preview.preview_id)
