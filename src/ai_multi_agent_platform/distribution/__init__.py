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
from .discovery import (
    CuratedCandidateReview,
    DiscoveryCandidate,
    RegistryDiscoverySource,
    curate_discovered_candidate,
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
from .reconciliation import RegistryPluginReconciliationError, reconcile_registry_plugins
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
from .technical_catalog import (
    TECHNICAL_CATEGORIES,
    TECHNICAL_COST_STATUSES,
    TECHNICAL_DEPLOYMENT_MODES,
    TECHNICAL_EVALUATION_STATUSES,
    TECHNICAL_LIFECYCLE_STATUSES,
    TECHNICAL_NETWORK_STATUSES,
    TechnicalMarketplaceMetadata,
    derive_technical_metadata,
    is_technical_component,
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
    "CuratedCandidateReview",
    "DiscoveryCandidate",
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
    "RegistryDiscoverySource",
    "RegistryInstallation",
    "RegistryInstallationSnapshot",
    "RegistryInstallationStore",
    "RegistryItem",
    "RegistryItemNotFoundError",
    "RegistryItemType",
    "RegistryPluginReconciliationError",
    "RegistryProvider",
    "RegistryQuery",
    "RegistryResourceService",
    "RegistrySignatureVerifier",
    "RegistrySource",
    "RegistryUnavailableError",
    "RegistryValidationContextResolver",
    "TECHNICAL_CATEGORIES",
    "TECHNICAL_COST_STATUSES",
    "TECHNICAL_DEPLOYMENT_MODES",
    "TECHNICAL_EVALUATION_STATUSES",
    "TECHNICAL_LIFECYCLE_STATUSES",
    "TECHNICAL_NETWORK_STATUSES",
    "TechnicalMarketplaceMetadata",
    "TrustStatus",
    "ValidationContext",
    "ValidationFinding",
    "VersionRange",
    "curate_discovered_candidate",
    "derive_technical_metadata",
    "has_errors",
    "is_technical_component",
    "load_hmac_signature_keys",
    "reconcile_registry_plugins",
    "register_distribution_control_plane",
    "registry_item_from_document",
    "validate_item",
    "validate_registry_item_document",
]
