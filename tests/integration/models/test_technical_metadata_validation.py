import pytest

from ai_multi_agent_platform.distribution import (
    DistributionService,
    LocalRegistryProvider,
    RegistryItem,
    RegistryItemType,
    RegistryQuery,
    RegistrySource,
)


def _technical_item(*tags: str) -> RegistryItem:
    return RegistryItem(
        item_id="technical-candidate",
        item_type=RegistryItemType.TOOL,
        name="Technical Candidate",
        description="Technical metadata fixture",
        version="1.0.0",
        publisher="example",
        source=RegistrySource(
            repository="https://example.invalid/candidate",
            package_reference="candidate@1.0.0",
        ),
        license="MIT",
        provenance="test fixture",
        tags=frozenset(tags),
        categories=frozenset({"evaluation"}),
    )


class _UnvalidatedProvider:
    provider_id = "unvalidated"

    def __init__(self, item: RegistryItem) -> None:
        self.item = item

    def search(self, query: RegistryQuery) -> tuple[RegistryItem, ...]:
        del query
        return (self.item,)

    def get(self, item_id: str, version: str | None = None) -> RegistryItem:
        del item_id, version
        return self.item

    def fetch_artifact(self, item_id: str, version: str) -> bytes:
        del item_id, version
        return b"fixture"


def test_local_provider_rejects_unsupported_structured_technical_tag() -> None:
    with pytest.raises(ValueError, match="unsupported cost"):
        LocalRegistryProvider((_technical_item("cost:free-ish"),))


def test_local_provider_rejects_conflicting_single_value_technical_tags() -> None:
    with pytest.raises(ValueError, match="conflicting lifecycle"):
        LocalRegistryProvider((_technical_item("lifecycle:candidate", "lifecycle:adopted"),))


def test_distribution_service_revalidates_metadata_from_replaceable_provider() -> None:
    malformed = _technical_item("lifecycle:candidate", "lifecycle:adopted")
    service = DistributionService(_UnvalidatedProvider(malformed))

    with pytest.raises(ValueError, match="conflicting lifecycle"):
        service.search()
    with pytest.raises(ValueError, match="conflicting lifecycle"):
        service.get(malformed.item_id, malformed.version)


def test_local_provider_allows_generic_registry_tags_without_technical_taxonomy() -> None:
    generic = RegistryItem(
        item_id="generic-tool",
        item_type=RegistryItemType.TOOL,
        name="Generic Tool",
        description="Generic Registry fixture",
        version="1.0.0",
        publisher="example",
        source=RegistrySource(
            repository="https://example.invalid/generic",
            package_reference="generic@1.0.0",
        ),
        license="MIT",
        provenance="test fixture",
        tags=frozenset({"cost:free-ish"}),
        categories=frozenset({"productivity"}),
    )

    LocalRegistryProvider((generic,))
