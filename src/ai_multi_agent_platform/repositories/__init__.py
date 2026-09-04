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
    "validate_git_revision",
]
