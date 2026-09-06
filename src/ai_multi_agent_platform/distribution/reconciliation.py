"""Fail-closed restart reconciliation for Registry-owned plugin installations."""

from __future__ import annotations

import hashlib

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.plugins import PluginRegistry

from .items import RegistryItem
from .models import RegistryItemType
from .plugin_adapter import PluginRegistryArtifactInstaller
from .provider import RegistryProvider
from .signatures import RegistrySignatureVerifier
from .state import RegistryInstallationSnapshot, RegistryInstallationStore


class RegistryPluginReconciliationError(RuntimeError):
    """Persisted Registry plugin state cannot be reconciled with its canonical #20 owner."""


async def reconcile_registry_plugins(
    provider: RegistryProvider,
    installations: RegistryInstallationStore,
    plugin_registry: PluginRegistry,
    *,
    signature_verifier: RegistrySignatureVerifier | None = None,
) -> tuple[str, ...]:
    """Restore previously installed Registry plugins into the canonical #20 registry.

    Reconciliation is not a new installation decision. It restores only a previously persisted
    Registry installation, never enables a runtime, never restores permission grants, and fails
    closed if the current catalog cannot reproduce the exact persisted plugin artifact/source.
    Non-plugin Registry installations remain owned by their already-persistent import domains.
    """

    installer = PluginRegistryArtifactInstaller(plugin_registry)
    restored: list[str] = []
    for installation in installations.list():
        snapshot = installation.current
        if snapshot.item_type is not None and snapshot.item_type is not RegistryItemType.PLUGIN:
            continue

        try:
            item = provider.get(snapshot.item_id, snapshot.version)
        except LookupError as exc:
            if snapshot.item_type is RegistryItemType.PLUGIN:
                raise RegistryPluginReconciliationError(
                    f"persisted Registry plugin {snapshot.item_id!r} "
                    f"version {snapshot.version!r} is missing from the configured catalog"
                ) from exc
            # Legacy v1 state did not persist item_type. If the item is no longer available there
            # is no safe basis for guessing that it was executable plugin code.
            continue

        if item.item_type is not RegistryItemType.PLUGIN:
            if snapshot.item_type is RegistryItemType.PLUGIN:
                raise RegistryPluginReconciliationError(
                    f"persisted Registry plugin {snapshot.item_id!r} changed item type"
                )
            continue

        _validate_snapshot(provider.provider_id, snapshot, item)
        artifact = provider.fetch_artifact(item.item_id, item.version)
        digest = hashlib.sha256(artifact).hexdigest()
        trusted_digest = snapshot.artifact_sha256 or item.integrity.sha256
        if trusted_digest is None:
            raise RegistryPluginReconciliationError(
                f"persisted Registry plugin {item.item_id!r} has no durable artifact digest"
            )
        if digest != trusted_digest:
            raise RegistryPluginReconciliationError(
                f"persisted Registry plugin {item.item_id!r} artifact digest changed"
            )
        if item.integrity.sha256 is not None and digest != item.integrity.sha256:
            raise RegistryPluginReconciliationError(
                f"persisted Registry plugin {item.item_id!r} fails catalog checksum validation"
            )
        if item.integrity.signature is not None:
            verified = (
                signature_verifier.verify(item, artifact)
                if signature_verifier is not None
                else None
            )
            if verified is not True:
                raise RegistryPluginReconciliationError(
                    f"persisted Registry plugin {item.item_id!r} signature cannot be verified"
                )

        try:
            current = plugin_registry.get(item.item_id)
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
        else:
            if current.plugin_version != item.version:
                raise RegistryPluginReconciliationError(
                    f"canonical plugin owner already contains {item.item_id!r} "
                    f"at version {current.plugin_version!r}, expected {item.version!r}"
                )

        await installer.install_verified_plugin(item, artifact)
        restored.append(item.item_id)

    return tuple(restored)


def _validate_snapshot(
    provider_id: str,
    snapshot: RegistryInstallationSnapshot,
    item: RegistryItem,
) -> None:
    mismatches: list[str] = []
    if snapshot.source_registry != provider_id:
        mismatches.append("registry provider")
    if snapshot.source_repository != item.source.repository:
        mismatches.append("source repository")
    if snapshot.package_reference != item.source.package_reference:
        mismatches.append("package reference")
    if snapshot.revision != item.source.revision:
        mismatches.append("source revision")
    if snapshot.license != item.license:
        mismatches.append("license")
    if snapshot.provenance != item.provenance:
        mismatches.append("provenance")
    if snapshot.item_type is not None and snapshot.item_type is not item.item_type:
        mismatches.append("item type")
    if mismatches:
        raise RegistryPluginReconciliationError(
            f"persisted Registry plugin {snapshot.item_id!r} changed " + ", ".join(mismatches)
        )
