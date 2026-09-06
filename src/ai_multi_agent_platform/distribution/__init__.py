"""Optional registry and distribution contracts for issue #81."""

from .canonical_router import (
    CanonicalDistributionRouter,
    PluginArtifactInstaller,
    PortabilityImportOwner,
)
from .composition import PlatformRegistryValidationContextResolver
from .control_plane import (
    REGISTRY_ACTIVATE_COMMAND,
    REGISTRY_COLLECTION,
    REGISTRY_PIN_COMMAND,
    REGISTRY_PREVIEW_COMMAND,
    REGISTRY_UNPIN_COMMAND,
    RegistryCommandHandlers,
    RegistryResourceService,
    RegistryValidationContextResolver,
    register_distribution_control_plane,
)
from .filesystem import FilesystemRegistryProvider
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
from .plugin_adapter import PluginRegistryArtifactInstaller
from .provider import RegistryItemNotFoundError, RegistryProvider, RegistryUnavailableError
from .schema import (
    REGISTRY_ITEM_SCHEMA_VERSION,
    registry_item_from_document,
    validate_registry_item_document,
)
from .service import DistributionPreview, DistributionRouter, DistributionService
from .signatures import (
    HmacSha256SignatureVerifier,
    RegistrySignatureVerifier,
    load_hmac_signature_keys,
)
from .state import (
    JsonRegistryInstallationStore,
    RegistryInstallation,
    RegistryInstallationSnapshot,
    RegistryInstallationStore,
)
from .validation import (
    FindingSeverity,
    ValidationContext,
    ValidationFinding,
    has_errors,
    validate_item,
)

__all__ = [
    "ArtifactIntegrity",
    "CanonicalDistributionRouter",
    "DistributionPreview",
    "DistributionRoute",
    "DistributionRouter",
    "DistributionService",
    "FilesystemRegistryProvider",
    "FindingSeverity",
    "HmacSha256SignatureVerifier",
    "InstalledRegistryItem",
    "JsonRegistryInstallationStore",
    "LocalRegistryProvider",
    "PlatformRegistryValidationContextResolver",
    "PluginArtifactInstaller",
    "PluginRegistryArtifactInstaller",
    "PortabilityImportOwner",
    "REGISTRY_ACTIVATE_COMMAND",
    "REGISTRY_COLLECTION",
    "REGISTRY_ITEM_SCHEMA_VERSION",
    "REGISTRY_PIN_COMMAND",
    "REGISTRY_PREVIEW_COMMAND",
    "REGISTRY_UNPIN_COMMAND",
    "RegistryCommandHandlers",
    "RegistryDependency",
    "RegistryInstallation",
    "RegistryInstallationSnapshot",
    "RegistryInstallationStore",
    "RegistryItem",
    "RegistryItemNotFoundError",
    "RegistryItemType",
    "RegistryProvider",
    "RegistryQuery",
    "RegistryResourceService",
    "RegistrySignatureVerifier",
    "RegistrySource",
    "RegistryUnavailableError",
    "RegistryValidationContextResolver",
    "TrustStatus",
    "ValidationContext",
    "ValidationFinding",
    "VersionRange",
    "has_errors",
    "load_hmac_signature_keys",
    "register_distribution_control_plane",
    "registry_item_from_document",
    "validate_item",
    "validate_registry_item_document",
]
