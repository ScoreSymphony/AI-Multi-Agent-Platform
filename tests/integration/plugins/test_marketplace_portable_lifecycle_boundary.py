from __future__ import annotations

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.distribution import (
    DistributionService,
    JsonRegistryInstallationStore,
    LocalRegistryProvider,
    RegistryCommandHandlers,
    RegistryItem,
    RegistryItemType,
    RegistrySource,
    RegistryValidationContextResolver,
    TrustStatus,
    ValidationContext,
)


class _StaticValidationContext(RegistryValidationContextResolver):
    async def resolve(self, context: RequestContext) -> ValidationContext:
        del context
        return ValidationContext("0.0.1")


class _RecordingRouter:
    def __init__(self) -> None:
        self.portable_calls: list[tuple[str, str]] = []

    async def install_plugin(self, item: RegistryItem, artifact: bytes) -> object:
        del item, artifact
        raise AssertionError("portable lifecycle fixture must not use plugin install")

    async def import_portable(self, item: RegistryItem, artifact: bytes) -> object:
        del artifact
        self.portable_calls.append((item.item_id, item.version))
        return item.item_id


def _item(kind: RegistryItemType, version: str) -> RegistryItem:
    item_id = f"portable.{kind.value}"
    return RegistryItem(
        item_id=item_id,
        item_type=kind,
        name=f"Portable {kind.value}",
        description=f"Legacy portable {kind.value} Marketplace fixture",
        version=version,
        publisher="tests",
        source=RegistrySource(
            "https://example.invalid/portable",
            f"{item_id}@{version}",
            revision=f"rev-{version}",
        ),
        license="MIT",
        provenance="portable-owner-boundary",
        trust_status=TrustStatus.REVIEWED,
    )


@pytest.mark.parametrize(
    "kind",
    (
        RegistryItemType.TOOL,
        RegistryItemType.CONNECTOR,
        RegistryItemType.TEMPLATE,
        RegistryItemType.WORKFLOW,
    ),
)
def test_marketplace_portable_import_fails_closed_and_registry_remains_compatible(
    tmp_path: Path,
    kind: RegistryItemType,
) -> None:
    first = _item(kind, "1.0.0")
    second = replace(
        first,
        version="1.1.0",
        source=RegistrySource(
            first.source.repository,
            f"{first.item_id}@1.1.0",
            revision="rev-1.1.0",
        ),
    )
    provider = LocalRegistryProvider(
        (first, second),
        {
            (first.item_id, first.version): b"portable-v1",
            (second.item_id, second.version): b"portable-v2",
        },
    )
    router = _RecordingRouter()
    installations = JsonRegistryInstallationStore(tmp_path / f"{kind.value}-installations.json")
    distribution = DistributionService(
        provider,
        router,
        installations=installations,
    )
    commands = RegistryCommandHandlers(
        distribution,
        _StaticValidationContext(),
    )
    request = RequestContext(
        f"request-{kind.value}",
        f"correlation-{kind.value}",
    )

    assert first.route.value == "portable_import"
    assert distribution.route_available(first) is False
    descriptor = distribution.kind_descriptor(first)
    assert descriptor is not None
    if kind in {RegistryItemType.TEMPLATE, RegistryItemType.WORKFLOW}:
        assert descriptor.supports_install is False
        assert descriptor.supports_update is False
        assert descriptor.supports_uninstall is False

    preview = asyncio.run(
        commands.marketplace_preview(
            request,
            first.item_id,
            {"version": first.version},
        )
    )
    assert preview["activation_allowed"] is False
    assert preview["item"]["route_available"] is False  # type: ignore[index]

    with pytest.raises(ContractError) as blocked_install:
        asyncio.run(
            commands.marketplace_install(
                request,
                first.item_id,
                {"version": first.version},
            )
        )
    assert blocked_install.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert blocked_install.value.details["marketplace_reason"] == "route_unavailable"
    assert router.portable_calls == []
    assert installations.get(first.item_id) is None

    legacy_install = asyncio.run(
        commands.activate(
            request,
            first.item_id,
            {"version": first.version},
        )
    )
    assert legacy_install["status"] == "applied"
    assert router.portable_calls == [(first.item_id, first.version)]
    installed = installations.get(first.item_id)
    assert installed is not None
    assert installed.current.version == first.version

    update_preview = asyncio.run(
        commands.marketplace_preview(
            request,
            second.item_id,
            {"version": second.version},
        )
    )
    assert update_preview["activation_allowed"] is False

    with pytest.raises(ContractError) as blocked_update:
        asyncio.run(
            commands.marketplace_update(
                request,
                second.item_id,
                {"version": second.version},
            )
        )
    assert blocked_update.value.code is ErrorCode.UNSUPPORTED_CAPABILITY
    assert blocked_update.value.details["marketplace_reason"] == "route_unavailable"
    assert router.portable_calls == [(first.item_id, first.version)]
    installed_after_block = installations.get(first.item_id)
    assert installed_after_block is not None
    assert installed_after_block.current.version == first.version
