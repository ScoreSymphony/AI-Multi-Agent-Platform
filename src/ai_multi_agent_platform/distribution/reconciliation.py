"""Fail-closed restart reconciliation for Registry-owned plugin installations."""

from __future__ import annotations

import hashlib

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.plugins import ExtensionType, PluginRegistry

from .items import RegistryItem
from .models import RegistryItemType
from .plugin_adapter import PluginRegistryArtifactInstaller
from .provider import RegistryProvider
from .signatures import RegistrySignatureVerifier
from .state import RegistryInstallationSnapshot, RegistryInstallationStore


_PLUGIN_BACKED_KINDS = {
    RegistryItemType.TOOL: ExtensionType.CAPABILITY_PROVIDER,
    RegistryItemType.CONNECTOR: ExtensionType.CONNECTOR_PROVIDER,
}


class RegistryPluginReconciliationError(RuntimeError):
    """Persisted Registry package state cannot be reconciled with its canonical plugin owner."""


async def reconcile_registry_plugins(
    provider: RegistryProvider,
    installations: RegistryInstallationStore,
    plugin_registry: PluginRegistry,
    *,
    signature_verifier: RegistrySignatureVerifier | None = None,
) -> tuple[str, ...]:
    """Restore Registry installations whose package lifecycle is owned by the plugin domain.

    Reconciliation restores only previously persisted plugin-backed installations. It never
    enables runtimes or restores permission grants. Skills, Applications and portable imports keep
    their own durable owner state.
    """

    installer = PluginRegistryArtifactInstaller(plugin_registry)
    restored: list[str] = []
    for installation in installations.list():
        item_id = await _restore_plugin_backed_installation(
            provider,
            installation.current,
            plugin_registry,
            installer,
            signature_verifier=signature_verifier,
        )
        if item_id is not None:
            restored.append(item_id)
    return tuple(restored)


async def _restore_plugin_backed_installation(
    provider: RegistryProvider,
    snapshot: RegistryInstallationSnapshot,
    plugin_registry: PluginRegistry,
    installer: PluginRegistryArtifactInstaller,
    *,
    signature_verifier: RegistrySignatureVerifier | None,
) -> str | None:
    if not _snapshot_may_be_plugin_backed(snapshot):
        return None

    item = _resolve_persisted_item(provider, snapshot)
    if item is None or not _validate_persisted_kind(snapshot, item):
        return None

    _validate_snapshot(provider.provider_id, snapshot, item)
    artifact = provider.fetch_artifact(item.item_id, item.version)
    _validate_artifact(snapshot, item, artifact, signature_verifier)
    _validate_expected_extension(item, artifact, installer)
    _validate_existing_owner_state(plugin_registry, item)

    await installer.install_verified_plugin(item, artifact)
    return item.item_id


def _snapshot_may_be_plugin_backed(snapshot: RegistryInstallationSnapshot) -> bool:
    return (
        snapshot.item_type is None
        or snapshot.item_type is RegistryItemType.PLUGIN
        or snapshot.item_type in _PLUGIN_BACKED_KINDS
    )


def _resolve_persisted_item(
    provider: RegistryProvider,
    snapshot: RegistryInstallationSnapshot,
) -> RegistryItem | None:
    try:
        return provider.get(snapshot.item_id, snapshot.version)
    except LookupError as exc:
        if snapshot.item_type is not None and _is_plugin_backed_kind(snapshot.item_type):
            raise RegistryPluginReconciliationError(
                f"persisted Registry component {snapshot.item_id!r} "
                f"version {snapshot.version!r} is missing from the configured catalog"
            ) from exc
        # Legacy state without item_type cannot safely be assumed to contain executable code.
        return None


def _validate_persisted_kind(
    snapshot: RegistryInstallationSnapshot,
    item: RegistryItem,
) -> bool:
    if not _is_plugin_backed_kind(item.item_type):
        if snapshot.item_type is not None and _is_plugin_backed_kind(snapshot.item_type):
            raise RegistryPluginReconciliationError(
                f"persisted Registry component {snapshot.item_id!r} changed item type"
            )
        return False
    if snapshot.item_type is not None and snapshot.item_type is not item.item_type:
        raise RegistryPluginReconciliationError(
            f"persisted Registry component {snapshot.item_id!r} changed item type"
        )
    return True


def _is_plugin_backed_kind(item_type: object) -> bool:
    return item_type is RegistryItemType.PLUGIN or item_type in _PLUGIN_BACKED_KINDS


def _validate_artifact(
    snapshot: RegistryInstallationSnapshot,
    item: RegistryItem,
    artifact: bytes,
    signature_verifier: RegistrySignatureVerifier | None,
) -> None:
    digest = hashlib.sha256(artifact).hexdigest()
    trusted_digest = snapshot.artifact_sha256 or item.integrity.sha256
    if trusted_digest is None:
        raise RegistryPluginReconciliationError(
            f"persisted Registry component {item.item_id!r} has no durable artifact digest"
        )
    if digest != trusted_digest:
        raise RegistryPluginReconciliationError(
            f"persisted Registry component {item.item_id!r} artifact digest changed"
        )
    if item.integrity.sha256 is not None and digest != item.integrity.sha256:
        raise RegistryPluginReconciliationError(
            f"persisted Registry component {item.item_id!r} fails catalog checksum validation"
        )
    if item.integrity.signature is not None:
        verified = (
            signature_verifier.verify(item, artifact) if signature_verifier is not None else None
        )
        if verified is not True:
            raise RegistryPluginReconciliationError(
                f"persisted Registry component {item.item_id!r} signature cannot be verified"
            )


def _validate_expected_extension(
    item: RegistryItem,
    artifact: bytes,
    installer: PluginRegistryArtifactInstaller,
) -> None:
    expected_extension = _PLUGIN_BACKED_KINDS.get(item.item_type)
    if expected_extension is None:
        return
    manifest = installer.validated_manifest(item, artifact)
    if not any(extension.extension_type is expected_extension for extension in manifest.extensions):
        raise RegistryPluginReconciliationError(
            f"persisted Registry {item.kind} {item.item_id!r} no longer declares "
            f"{expected_extension.value}"
        )


def _validate_existing_owner_state(
    plugin_registry: PluginRegistry,
    item: RegistryItem,
) -> None:
    try:
        current = plugin_registry.get(item.item_id)
    except ContractError as exc:
        if exc.code is ErrorCode.NOT_FOUND:
            return
        raise
    if current.plugin_version != item.version:
        raise RegistryPluginReconciliationError(
            f"canonical plugin owner already contains {item.item_id!r} "
            f"at version {current.plugin_version!r}, expected {item.version!r}"
        )


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
            f"persisted Registry component {snapshot.item_id!r} changed " + ", ".join(mismatches)
        )
