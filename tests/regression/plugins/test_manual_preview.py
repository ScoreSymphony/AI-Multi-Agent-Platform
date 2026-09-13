from ai_multi_agent_platform.distribution import (
    DistributionRoute,
    DistributionService,
    LocalRegistryProvider,
    RegistryItem,
    RegistryItemType,
    RegistrySource,
    TrustStatus,
    ValidationContext,
)


def _item(*, item_id: str, manual: bool) -> RegistryItem:
    return RegistryItem(
        item_id=item_id,
        item_type=RegistryItemType.TOOL,
        name=item_id.replace("-", " ").title(),
        description="Registry preview fixture",
        version="1.0.0",
        publisher="example",
        source=RegistrySource(
            repository=f"https://example.invalid/{item_id}",
            package_reference=f"{item_id}@1.0.0",
        ),
        license="MIT",
        provenance="test fixture",
        categories=frozenset({"evaluation"}),
        trust_status=TrustStatus.UNTRUSTED,
        distribution_route=DistributionRoute.MANUAL if manual else None,
    )


def test_manual_candidate_preview_is_non_activatable_even_without_validation_errors() -> None:
    item = _item(item_id="manual-candidate", manual=True)
    provider = LocalRegistryProvider(
        (item,),
        artifacts={(item.item_id, item.version): b"reference metadata"},
    )
    service = DistributionService(provider)

    preview = service.preview(
        item.item_id,
        item.version,
        ValidationContext(platform_version="1.0.0"),
    )

    assert preview.route is DistributionRoute.MANUAL
    assert preview.activation_allowed is False
    assert not any(finding.severity.value == "error" for finding in preview.findings)


def test_portable_registry_preview_keeps_existing_activation_semantics() -> None:
    item = _item(item_id="portable-tool", manual=False)
    provider = LocalRegistryProvider(
        (item,),
        artifacts={(item.item_id, item.version): b"portable artifact"},
    )
    service = DistributionService(provider)

    preview = service.preview(
        item.item_id,
        item.version,
        ValidationContext(platform_version="1.0.0"),
    )

    assert preview.route is DistributionRoute.PORTABLE_IMPORT
    assert preview.activation_allowed is True
    assert not any(finding.severity.value == "error" for finding in preview.findings)
