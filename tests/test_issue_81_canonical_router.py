from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.distribution import (
    CanonicalDistributionRouter,
    RegistryItem,
    RegistryItemType,
    RegistrySource,
)


@dataclass(frozen=True)
class Inspection:
    package_id: str


@dataclass(frozen=True)
class Preview:
    preview_id: str
    ready: bool


class RecordingPortability:
    def __init__(self, *, ready: bool = True) -> None:
        self.ready = ready
        self.calls: list[tuple[str, object]] = []

    def validate_package_document(self, document: object) -> Inspection:
        self.calls.append(("validate", document))
        return Inspection("pkg_1")

    def preview_import(self, package_id: str) -> Preview:
        self.calls.append(("preview", package_id))
        return Preview("preview_1", self.ready)

    async def execute_import(self, preview_id: str) -> object:
        self.calls.append(("execute", preview_id))
        return {"report_id": "import_1"}


class RecordingPluginInstaller:
    def __init__(self) -> None:
        self.calls: list[tuple[str, bytes]] = []

    async def install_verified_plugin(self, item: RegistryItem, artifact: bytes) -> object:
        self.calls.append((item.item_id, artifact))
        return {"plugin_id": item.item_id}


def _item(item_type: RegistryItemType) -> RegistryItem:
    return RegistryItem(
        item_id=f"example.{item_type.value}",
        item_type=item_type,
        name="Example",
        description="Owner handoff fixture",
        version="1.0.0",
        publisher="example",
        source=RegistrySource("https://example.invalid/repo", "asset@1.0.0"),
        license="MIT",
        provenance="source-release",
    )


def test_portable_artifact_uses_canonical_preview_then_execute_workflow() -> None:
    portability = RecordingPortability()
    router = CanonicalDistributionRouter(portability=portability)

    result = asyncio.run(
        router.import_portable(_item(RegistryItemType.TEMPLATE), b'{"format_version":"1"}')
    )

    assert result == {"report_id": "import_1"}
    assert portability.calls == [
        ("validate", {"format_version": "1"}),
        ("preview", "pkg_1"),
        ("execute", "preview_1"),
    ]


def test_portable_artifact_fails_closed_when_owner_preview_is_not_ready() -> None:
    portability = RecordingPortability(ready=False)
    router = CanonicalDistributionRouter(portability=portability)

    with pytest.raises(ContractError, match="not ready"):
        asyncio.run(router.import_portable(_item(RegistryItemType.WORKFLOW), b"{}"))

    assert portability.calls == [("validate", {}), ("preview", "pkg_1")]


def test_portable_artifact_rejects_non_json_before_owner_mutation() -> None:
    portability = RecordingPortability()
    router = CanonicalDistributionRouter(portability=portability)

    with pytest.raises(ContractError, match="valid UTF-8 JSON"):
        asyncio.run(router.import_portable(_item(RegistryItemType.AGENT), b"not-json"))

    assert portability.calls == []


def test_plugin_artifact_requires_explicit_owner_installer() -> None:
    item = _item(RegistryItemType.PLUGIN)
    with pytest.raises(ContractError, match="#20 artifact installer"):
        asyncio.run(CanonicalDistributionRouter().install_plugin(item, b"plugin-package"))

    installer = RecordingPluginInstaller()
    result = asyncio.run(
        CanonicalDistributionRouter(plugin_installer=installer).install_plugin(
            item,
            b"plugin-package",
        )
    )
    assert result == {"plugin_id": item.item_id}
    assert installer.calls == [(item.item_id, b"plugin-package")]
