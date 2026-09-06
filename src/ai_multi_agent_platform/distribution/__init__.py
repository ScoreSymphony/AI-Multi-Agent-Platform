"""Optional registry and distribution contracts for issue #81."""

from .control_plane import (
    REGISTRY_COLLECTION,
    REGISTRY_PREVIEW_COMMAND,
    RegistryCommandHandlers,
    RegistryResourceService,
    RegistryValidationContextResolver,
    register_distribution_control_plane,
)
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
    "REGISTRY_COLLECTION",
    "REGISTRY_ITEM_SCHEMA_VERSION",
    "REGISTRY_PREVIEW_COMMAND",
    "RegistryCommandHandlers",
    "RegistryDependency",
    "RegistryItem",
    "RegistryItemNotFoundError",
    "RegistryItemType",
    "RegistryProvider",
    "RegistryQuery",
    "RegistryResourceService",
    "RegistrySource",
    "RegistryUnavailableError",
    "RegistryValidationContextResolver",
    "TrustStatus",
    "ValidationContext",
    "ValidationFinding",
    "VersionRange",
    "has_errors",
    "register_distribution_control_plane",
    "validate_item",
    "validate_registry_item_document",
]
