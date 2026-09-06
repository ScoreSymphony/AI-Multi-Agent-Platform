"""Optional registry and distribution contracts for issue #81."""

from .items import InstalledRegistryItem, RegistryItem, RegistryQuery
from .local import LocalRegistryProvider
from .models import (
    ArtifactIntegrity,
    DistributionRoute,
    RegistryDependency,
    RegistryItemType,
    RegistrySource,
    TrustStatus,
    VersionRange,
)
from .provider import RegistryItemNotFoundError, RegistryProvider, RegistryUnavailableError
from .schema import REGISTRY_ITEM_SCHEMA_VERSION, validate_registry_item_document
from .service import DistributionPreview, DistributionRouter, DistributionService
from .validation import (
    FindingSeverity,
    ValidationContext,
    ValidationFinding,
    has_errors,
    validate_item,
)

__all__ = [
    "ArtifactIntegrity",
    "DistributionPreview",
    "DistributionRoute",
    "DistributionRouter",
    "DistributionService",
    "FindingSeverity",
    "InstalledRegistryItem",
    "LocalRegistryProvider",
    "REGISTRY_ITEM_SCHEMA_VERSION",
    "RegistryDependency",
    "RegistryItem",
    "RegistryItemNotFoundError",
    "RegistryItemType",
    "RegistryProvider",
    "RegistryQuery",
    "RegistrySource",
    "RegistryUnavailableError",
    "TrustStatus",
    "ValidationContext",
    "ValidationFinding",
    "VersionRange",
    "has_errors",
    "validate_item",
    "validate_registry_item_document",
]
