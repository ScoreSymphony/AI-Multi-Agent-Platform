"""Query parsing and ordering for the unified Marketplace Control Plane."""

from __future__ import annotations

from dataclasses import dataclass

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import PageQuery

from .control_plane_projection import _json_strings
from .items import RegistryQuery
from .models import RegistryMaturity, TrustStatus, version_key

MARKETPLACE_SORT_FIELDS = frozenset(
    {
        "id",
        "item_id",
        "item_type",
        "kind",
        "name",
        "description",
        "version",
        "publisher",
        "source_registry",
        "source",
        "license",
        "provenance",
        "minimum_platform_version",
        "maximum_platform_version",
        "trust_status",
        "trust",
        "maturity",
        "stability",
        "review_reference",
        "released_at",
        "release_date",
        "changelog",
        "deprecated",
        "yanked",
        "route",
        "route_available",
        "installed",
        "installed_version",
        "pinned_version",
        "update_available",
    }
)


@dataclass(frozen=True, slots=True)
class RegistryQueryPlan:
    query: RegistryQuery
    sources: frozenset[str] = frozenset()
    installed: bool | None = None
    update_available: bool | None = None
    deprecated: bool | None = None
    yanked: bool | None = None
    compatible: bool | None = None
    compatibility_platform_version: str | None = None


def registry_query(query: PageQuery) -> RegistryQueryPlan:
    filters = dict(query.filters or {})
    item_types = frozenset(_merge_filter_values(filters, "item_type", "kind"))
    tags = frozenset(_values(filters.pop("tag", None)))
    categories = frozenset(_values(filters.pop("category", None)))
    licenses = frozenset(_values(filters.pop("license", None)))
    publishers = frozenset(_values(filters.pop("publisher", None)))
    sources = frozenset(_merge_filter_values(filters, "source_registry", "source"))
    capabilities = frozenset(_values(filters.pop("required_capability", None)))
    raw_trust = _merge_filter_values(filters, "trust_status", "trust")
    try:
        trust_statuses = frozenset(TrustStatus(value) for value in raw_trust)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "invalid Marketplace trust status",
            details={"field": "trust"},
        ) from exc

    raw_maturity = _merge_filter_values(filters, "maturity", "stability")
    try:
        maturities = frozenset(RegistryMaturity(value) for value in raw_maturity)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "invalid Marketplace maturity",
            details={"field": "maturity"},
        ) from exc

    platform_version = filters.pop("platform_version", None)
    update_for_item_id = filters.pop("update_for_item_id", None)
    installed = _optional_bool(filters.pop("installed", None), default=None)
    update_available = _optional_bool(filters.pop("update_available", None), default=None)
    deprecated = _optional_bool(filters.pop("deprecated", None), default=None)
    yanked = _optional_bool(filters.pop("yanked", None), default=None)
    compatible = _optional_bool(filters.pop("compatible", None), default=None)
    include_deprecated = bool(
        _optional_bool(filters.pop("include_deprecated", None), default=False)
    )
    include_yanked = bool(_optional_bool(filters.pop("include_yanked", None), default=False))
    technical_only = bool(_optional_bool(filters.pop("technical_component", None), default=False))
    if deprecated is not None:
        include_deprecated = True
    if yanked is not None:
        include_yanked = True
    if filters:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "unsupported Marketplace filter(s): " + ", ".join(sorted(filters)),
        )

    try:
        resolved_query = RegistryQuery(
            text=query.search,
            item_types=item_types,
            tags=tags,
            categories=categories,
            licenses=licenses,
            publishers=publishers,
            required_capabilities=capabilities,
            trust_statuses=trust_statuses,
            maturities=maturities,
            platform_version=platform_version if compatible is not False else None,
            include_deprecated=include_deprecated,
            include_yanked=include_yanked,
            update_for_item_id=update_for_item_id,
            technical_only=technical_only,
        )
    except ValueError as exc:
        raise ContractError(ErrorCode.INVALID_REQUEST, str(exc)) from exc
    return RegistryQueryPlan(
        query=resolved_query,
        sources=sources,
        installed=installed,
        update_available=update_available,
        deprecated=deprecated,
        yanked=yanked,
        compatible=compatible,
        compatibility_platform_version=platform_version,
    )


def validate_marketplace_sort(sort: str) -> None:
    if sort not in MARKETPLACE_SORT_FIELDS:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "unsupported Marketplace sort field",
            details={
                "field": "sort",
                "supported": _json_strings(sorted(MARKETPLACE_SORT_FIELDS)),
            },
        )


def marketplace_resource_sort_key(
    resource: dict[str, JsonValue],
    sort: str,
) -> tuple[object, str]:
    canonical_sort = {
        "kind": "kind",
        "item_type": "kind",
        "release_date": "released_at",
        "source": "source_registry",
        "stability": "maturity",
    }.get(sort, sort)
    raw = resource.get(canonical_sort)
    if canonical_sort == "version":
        primary: object = version_key(str(raw))
    elif isinstance(raw, str):
        primary = raw.casefold()
    elif raw is None:
        primary = ""
    else:
        primary = str(raw)
    return primary, str(resource.get("qualified_id") or resource.get("id", ""))


def required_version(payload: dict[str, JsonValue], command: str) -> str:
    version = payload.get("version")
    if not isinstance(version, str) or not version.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{command} requires version")
    return version


def optional_source_registry(payload: dict[str, JsonValue]) -> str | None:
    value = payload.get("source_registry")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "source_registry must be a non-blank string",
            details={"field": "source_registry"},
        )
    return value.strip()


def split_resource_id(resource_id: str) -> tuple[str, str | None, str | None]:
    source_registry: str | None = None
    source, source_separator, unqualified = resource_id.partition("::")
    if source_separator:
        if not source.strip() or not unqualified:
            raise ContractError(ErrorCode.INVALID_REQUEST, "invalid registry resource id")
        source_registry = source.strip()
        resource_id = unqualified

    item_id, separator, version = resource_id.rpartition("@")
    if not separator:
        return resource_id, None, source_registry
    if not item_id or not version:
        raise ContractError(ErrorCode.INVALID_REQUEST, "invalid registry resource id")
    return item_id, version, source_registry


def _merge_filter_values(
    filters: dict[str, str],
    legacy_name: str,
    marketplace_name: str,
) -> tuple[str, ...]:
    values = [
        *_values(filters.pop(legacy_name, None)),
        *_values(filters.pop(marketplace_name, None)),
    ]
    return tuple(dict.fromkeys(values))


def _values(value: str | None) -> tuple[str, ...]:
    if value is None:
        return ()
    values = tuple(part.strip() for part in value.split(",") if part.strip())
    if not values:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "Registry filter value must be non-blank",
        )
    return values


def _optional_bool(value: str | None, *, default: bool | None) -> bool | None:
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ContractError(
        ErrorCode.INVALID_REQUEST,
        "Registry boolean filter must be true or false",
    )
