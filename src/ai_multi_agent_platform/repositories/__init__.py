"""Provider-neutral repository and Git integration."""

from .capabilities import (
    EXTERNAL_SIDE_EFFECT_OPERATIONS,
    LOCAL_GIT_CAPABILITIES,
    LOCAL_WRITE_OPERATIONS,
    READ_OPERATIONS,
    repository_capability,
    repository_capability_specs,
)
from .contracts import RepositoryProvider
from .events import RepositoryEventBridge, repository_platform_event_id, repository_resource_payload
from .local_git import LocalGitRepositoryProvider
from .models import (
    RepositoryCapability,
    RepositoryCommit,
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
from .service import (
    RepositoryBinding,
    RepositoryCallContext,
    RepositoryProvenanceStore,
    RepositoryRegistry,
    RepositoryService,
)
from .workspace import RepositoryWorkspaceSourceResolver

__all__ = [
    "EXTERNAL_SIDE_EFFECT_OPERATIONS",
    "LOCAL_GIT_CAPABILITIES",
    "LOCAL_WRITE_OPERATIONS",
    "LocalGitRepositoryProvider",
    "READ_OPERATIONS",
    "RepositoryBinding",
    "RepositoryCallContext",
    "RepositoryCapability",
    "RepositoryCommit",
    "RepositoryConnection",
    "RepositoryDiff",
    "RepositoryEventBridge",
    "RepositoryOperation",
    "RepositoryProvenanceStore",
    "RepositoryProvider",
    "RepositoryReference",
    "RepositoryRegistry",
    "RepositoryRevision",
    "RepositoryRunProvenance",
    "RepositoryService",
    "RepositoryStatus",
    "RepositoryTree",
    "RepositoryTreeEntry",
    "RepositoryVisibility",
    "RepositoryWorkspaceSourceResolver",
    "repository_capability",
    "repository_capability_specs",
    "repository_platform_event_id",
    "repository_resource_payload",
    "validate_git_revision",
]
