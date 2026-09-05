"""Provider-neutral repository and Git integration."""

from .capabilities import (
    EXTERNAL_SIDE_EFFECT_OPERATIONS,
    LOCAL_GIT_CAPABILITIES,
    LOCAL_WRITE_OPERATIONS,
    READ_OPERATIONS,
    repository_capability,
    repository_capability_specs,
)
from .capability_bridge import RepositoryActorResolver, RepositoryCapabilityProvider
from .catalog import (
    RepositoryBindingRecord,
    RepositoryProviderFactory,
    RepositoryRegistryBootstrap,
    SqliteRepositoryBindingCatalog,
    connector_repository_factory,
    local_git_repository_factory,
)
from .connector_repository import ConnectorRepositoryProvider
from .contracts import RepositoryProvider
from .events import RepositoryEventBridge, repository_platform_event_id, repository_resource_payload
from .local_git import LocalGitRepositoryProvider
from .management import RepositoryDiscoveryResolver, RepositoryManagementService
from .models import (
    RepositoryCapability,
    RepositoryCommit,
    RepositoryCommitInfo,
    RepositoryConnection,
    RepositoryDiff,
    RepositoryOperation,
    RepositoryReference,
    RepositoryRevision,
    RepositoryRunProvenance,
    RepositoryStatus,
    RepositoryTree,
    RepositoryTreeEntry,
    RepositoryVisibility,
    validate_git_revision,
)
from .persistence import SqliteRepositoryProvenanceStore
from .run_integration import RepositoryRunArtifactBundle, RepositoryRunIntegration
from .service import (
    RepositoryBinding,
    RepositoryCallContext,
    RepositoryProvenanceStore,
    RepositoryRegistry,
    RepositoryService,
)
from .workspace import RepositoryWorkspaceSourceResolver

__all__ = [
    "ConnectorRepositoryProvider",
    "EXTERNAL_SIDE_EFFECT_OPERATIONS",
    "LOCAL_GIT_CAPABILITIES",
    "LOCAL_WRITE_OPERATIONS",
    "LocalGitRepositoryProvider",
    "READ_OPERATIONS",
    "RepositoryActorResolver",
    "RepositoryBinding",
    "RepositoryBindingRecord",
    "RepositoryCallContext",
    "RepositoryCapability",
    "RepositoryCapabilityProvider",
    "RepositoryCommit",
    "RepositoryCommitInfo",
    "RepositoryConnection",
    "RepositoryDiff",
    "RepositoryDiscoveryResolver",
    "RepositoryEventBridge",
    "RepositoryManagementService",
    "RepositoryOperation",
    "RepositoryProvenanceStore",
    "RepositoryProvider",
    "RepositoryProviderFactory",
    "RepositoryReference",
    "RepositoryRegistry",
    "RepositoryRegistryBootstrap",
    "RepositoryRevision",
    "RepositoryRunArtifactBundle",
    "RepositoryRunIntegration",
    "RepositoryRunProvenance",
    "RepositoryService",
    "RepositoryStatus",
    "RepositoryTree",
    "RepositoryTreeEntry",
    "RepositoryVisibility",
    "RepositoryWorkspaceSourceResolver",
    "SqliteRepositoryBindingCatalog",
    "SqliteRepositoryProvenanceStore",
    "connector_repository_factory",
    "local_git_repository_factory",
    "repository_capability",
    "repository_capability_specs",
    "repository_platform_event_id",
    "repository_resource_payload",
    "validate_git_revision",
]
