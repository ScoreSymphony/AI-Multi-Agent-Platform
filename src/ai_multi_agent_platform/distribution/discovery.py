"""Fail-closed discovery-source seam for the curated technical Marketplace.

Discovery sources may suggest external projects, but they never gain installation or
trust authority. Promotion into the curated Registry catalog requires an explicit
review object containing the verified metadata that #638 requires.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .items import RegistryItem
from .models import DistributionRoute, RegistryItemType, RegistrySource, TrustStatus


@dataclass(frozen=True, slots=True)
class DiscoveryCandidate:
    source_id: str
    external_id: str
    name: str
    source_repository: str | None = None
    license: str | None = None
    project_status: str | None = None
    provenance: str | None = None


class RegistryDiscoverySource(Protocol):
    """Untrusted metadata source; deliberately has no fetch/install/activate API."""

    @property
    def source_id(self) -> str: ...

    def discover(self, query: str | None = None) -> tuple[DiscoveryCandidate, ...]: ...


@dataclass(frozen=True, slots=True)
class CuratedCandidateReview:
    """Human/governance-reviewed facts required before catalog promotion."""

    item_id: str
    item_type: RegistryItemType
    description: str
    version: str
    publisher: str
    source_repository: str
    package_reference: str
    license: str
    provenance: str
    categories: frozenset[str]
    deployment_modes: frozenset[str] = frozenset({"unknown"})
    cost_status: str = "unknown"
    network_status: str = "unknown"
    review_reference: str | None = None
    resource_class: str | None = None


def curate_discovered_candidate(
    candidate: DiscoveryCandidate,
    review: CuratedCandidateReview,
) -> RegistryItem:
    """Promote reviewed metadata into a discovery/evaluation-only Registry item.

    The result is always untrusted, manual-only and evaluation-required. A discovery
    source can therefore never bypass #81 activation, #15 authorization or the normal
    external-component adoption decision.
    """

    for value, label in (
        (candidate.source_id, "discovery source_id"),
        (candidate.external_id, "discovery external_id"),
        (candidate.name, "discovery name"),
        (review.source_repository, "review source_repository"),
        (review.license, "review license"),
        (review.provenance, "review provenance"),
    ):
        if not value.strip():
            raise ValueError(f"{label} must be non-blank")
    if candidate.source_repository and candidate.source_repository != review.source_repository:
        raise ValueError("reviewed source_repository does not match discovered source identity")
    if candidate.license and candidate.license != review.license:
        raise ValueError("reviewed license does not match discovered license metadata")
    if not review.categories:
        raise ValueError("curated technical candidate requires at least one category")

    tags = {
        "candidate",
        "external-component",
        "discovery-source:" + candidate.source_id,
        "lifecycle:candidate",
        "evaluation:required",
        "cost:" + review.cost_status,
        "network:" + review.network_status,
        *("deployment:" + mode for mode in review.deployment_modes),
    }
    if review.resource_class:
        tags.add("resource:" + review.resource_class)

    return RegistryItem(
        item_id=review.item_id,
        item_type=review.item_type,
        name=candidate.name,
        description=review.description,
        version=review.version,
        publisher=review.publisher,
        source=RegistrySource(
            repository=review.source_repository,
            package_reference=review.package_reference,
        ),
        license=review.license,
        provenance=(
            f"{review.provenance} Discovery source {candidate.source_id!r} supplied only an "
            "untrusted lead; this curated entry reflects explicit review metadata."
        ),
        tags=frozenset(tags),
        categories=review.categories,
        trust_status=TrustStatus.UNTRUSTED,
        review_reference=review.review_reference,
        distribution_route=DistributionRoute.MANUAL,
    )
