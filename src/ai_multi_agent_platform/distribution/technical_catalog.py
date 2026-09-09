"""Technical Marketplace taxonomy layered on the generic Registry contract.

Issue #638 intentionally keeps #81's RegistryItem schema generic. Product-facing
technical metadata is therefore encoded with a small, versioned tag vocabulary and
canonical Registry categories, then derived into a typed projection for UI/tests and
future discovery-source importers.
"""

from __future__ import annotations

from dataclasses import dataclass

from .items import RegistryItem

TECHNICAL_CATEGORIES: tuple[str, ...] = (
    "code-intelligence",
    "coding-agent",
    "agent-framework",
    "specification-and-skills",
    "memory-and-context",
    "evaluation",
    "security",
    "browser-and-execution",
    "inference-runtime",
    "retrieval",
    "model-and-dataset-tooling",
    "music-ai",
)

TECHNICAL_LIFECYCLE_STATUSES: tuple[str, ...] = (
    "discovered",
    "candidate",
    "pilot",
    "adopted",
    "reference",
    "deferred",
    "rejected",
    "deprecated",
    "unknown",
)
TECHNICAL_EVALUATION_STATUSES: tuple[str, ...] = (
    "required",
    "pending",
    "in-progress",
    "passed",
    "failed",
    "not-required",
    "unknown",
)
TECHNICAL_COST_STATUSES: tuple[str, ...] = (
    "compatible",
    "conditional",
    "incompatible",
    "unknown",
)
TECHNICAL_DEPLOYMENT_MODES: tuple[str, ...] = (
    "local",
    "self-hosted",
    "cli",
    "library",
    "service",
    "desktop",
    "extension",
    "browser",
    "hosted",
    "unknown",
)
TECHNICAL_NETWORK_STATUSES: tuple[str, ...] = (
    "none",
    "optional",
    "required",
    "unknown",
)


@dataclass(frozen=True, slots=True)
class TechnicalMarketplaceMetadata:
    """Typed product projection derived from Registry categories/tags."""

    categories: tuple[str, ...]
    lifecycle_status: str
    evaluation_status: str
    deployment_modes: tuple[str, ...]
    cost_status: str
    network_status: str
    provider_requirements: tuple[str, ...]
    alternatives: tuple[str, ...]
    resource_class: str | None
    architecture_reference: str | None
    decision_reference: str | None
    evaluation_reference: str | None

    @property
    def evaluation_required(self) -> bool:
        return self.evaluation_status in {"required", "pending", "in-progress"}


def is_technical_component(item: RegistryItem) -> bool:
    return bool(set(item.categories).intersection(TECHNICAL_CATEGORIES))


def derive_technical_metadata(item: RegistryItem) -> TechnicalMarketplaceMetadata | None:
    """Derive #638 metadata without creating a second Registry item schema.

    Missing facts remain explicitly ``unknown``/``None``. Conflicting or unsupported
    structured tags fail closed rather than silently choosing one value.
    """

    categories = tuple(sorted(set(item.categories).intersection(TECHNICAL_CATEGORIES)))
    if not categories:
        return None

    return TechnicalMarketplaceMetadata(
        categories=categories,
        lifecycle_status=_single_tag_value(
            item,
            "lifecycle:",
            TECHNICAL_LIFECYCLE_STATUSES,
            default="unknown",
        ),
        evaluation_status=_single_tag_value(
            item,
            "evaluation:",
            TECHNICAL_EVALUATION_STATUSES,
            default="unknown",
        ),
        deployment_modes=_multi_tag_values(
            item,
            "deployment:",
            TECHNICAL_DEPLOYMENT_MODES,
            default=("unknown",),
        ),
        cost_status=_single_tag_value(
            item,
            "cost:",
            TECHNICAL_COST_STATUSES,
            default="unknown",
        ),
        network_status=_single_tag_value(
            item,
            "network:",
            TECHNICAL_NETWORK_STATUSES,
            default="unknown",
        ),
        provider_requirements=_freeform_values(item, "provider:"),
        alternatives=_freeform_values(item, "alternative:"),
        resource_class=_optional_freeform_value(item, "resource:"),
        architecture_reference=_optional_freeform_value(item, "architecture-ref:"),
        decision_reference=_optional_freeform_value(item, "decision-ref:"),
        evaluation_reference=_optional_freeform_value(item, "evaluation-ref:"),
    )


def _single_tag_value(
    item: RegistryItem,
    prefix: str,
    allowed: tuple[str, ...],
    *,
    default: str,
) -> str:
    values = _freeform_values(item, prefix)
    if not values:
        return default
    if len(values) != 1:
        raise ValueError(
            f"technical Marketplace item {item.item_id!r} has conflicting {prefix} tags"
        )
    value = values[0]
    if value not in allowed:
        raise ValueError(
            f"technical Marketplace item {item.item_id!r} has unsupported {prefix}{value} tag"
        )
    return value


def _multi_tag_values(
    item: RegistryItem,
    prefix: str,
    allowed: tuple[str, ...],
    *,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    values = _freeform_values(item, prefix)
    if not values:
        return default
    unsupported = tuple(value for value in values if value not in allowed)
    if unsupported:
        raise ValueError(
            f"technical Marketplace item {item.item_id!r} has unsupported {prefix} tag(s): "
            + ", ".join(unsupported)
        )
    return values


def _freeform_values(item: RegistryItem, prefix: str) -> tuple[str, ...]:
    return tuple(sorted(tag[len(prefix) :] for tag in item.tags if tag.startswith(prefix)))


def _optional_freeform_value(item: RegistryItem, prefix: str) -> str | None:
    values = _freeform_values(item, prefix)
    if not values:
        return None
    if len(values) != 1:
        raise ValueError(
            f"technical Marketplace item {item.item_id!r} has conflicting {prefix} tags"
        )
    return values[0]
