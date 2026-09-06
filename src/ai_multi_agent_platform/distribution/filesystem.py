"""Filesystem-backed Registry provider for offline/self-hosted deployments."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .items import RegistryItem, RegistryQuery
from .local import LocalRegistryProvider
from .provider import RegistryItemNotFoundError
from .schema import registry_item_from_document

CATALOG_SCHEMA_VERSION = "1"


class FilesystemRegistryProvider:
    """Read a canonical local catalog plus artifact files without a hosted service."""

    def __init__(self, catalog_path: Path) -> None:
        self._catalog_path = catalog_path.resolve()
        document = json.loads(self._catalog_path.read_text(encoding="utf-8"))
        if not isinstance(document, dict):
            raise ValueError("registry catalog must be a JSON object")
        if document.get("schema_version") != CATALOG_SCHEMA_VERSION:
            raise ValueError("unsupported registry catalog schema_version")
        provider_id = document.get("provider_id", "local")
        if not isinstance(provider_id, str) or not provider_id.strip():
            raise ValueError("registry catalog provider_id must be a non-blank string")
        raw_items = document.get("items")
        if not isinstance(raw_items, list):
            raise ValueError("registry catalog must contain an items array")

        items: list[RegistryItem] = []
        artifacts: dict[tuple[str, str], Path] = {}
        root = self._catalog_path.parent.resolve()
        for entry in raw_items:
            if not isinstance(entry, dict):
                raise ValueError("registry catalog item must be an object")
            metadata = entry.get("metadata")
            if not isinstance(metadata, dict):
                raise ValueError("registry catalog item metadata must be an object")
            item = registry_item_from_document(_string_keyed(metadata))
            artifact = entry.get("artifact")
            if not isinstance(artifact, str) or not artifact.strip():
                raise ValueError("registry catalog item artifact must be a non-blank path")
            artifact_path = (root / artifact).resolve()
            if not artifact_path.is_relative_to(root):
                raise ValueError("registry artifact path must remain inside the catalog directory")
            identity = (item.item_id, item.version)
            if identity in artifacts:
                raise ValueError("registry catalog contains duplicate item/version identities")
            items.append(item)
            artifacts[identity] = artifact_path

        self._metadata = LocalRegistryProvider(items, provider_id=provider_id)
        self._artifacts = artifacts

    @property
    def provider_id(self) -> str:
        return self._metadata.provider_id

    def search(self, query: RegistryQuery) -> tuple[RegistryItem, ...]:
        return self._metadata.search(query)

    def get(self, item_id: str, version: str | None = None) -> RegistryItem:
        return self._metadata.get(item_id, version)

    def fetch_artifact(self, item_id: str, version: str) -> bytes:
        path = self._artifacts.get((item_id, version))
        if path is None:
            raise RegistryItemNotFoundError(
                f"artifact for registry item {item_id!r} version {version!r} not found"
            )
        try:
            return path.read_bytes()
        except OSError as exc:
            raise RegistryItemNotFoundError(
                f"artifact for registry item {item_id!r} version {version!r} is unavailable"
            ) from exc


def _string_keyed(value: dict[object, object]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise ValueError("registry metadata keys must be strings")
        result[key] = item
    return result
