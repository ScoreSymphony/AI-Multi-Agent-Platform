"""Required provider-neutral repository/code-intelligence core and optional enhancements."""

from .baseline import BaselineRepositoryIntelligenceProvider, RepositorySnapshotLoader
from .capabilities import (
    RepositoryIntelligenceOperation,
    repository_intelligence_capability_specs,
)
from .models import (
    RepositoryIntelligenceFreshness,
    RepositoryIntelligenceProvenance,
    RepositoryIntelligenceStateClass,
)
from .workspace import (
    WorkspaceAwareRepositoryIntelligenceProvider,
    WorkspaceRepositorySnapshot,
    WorkspaceRepositorySnapshotLoader,
)

__all__ = [
    "BaselineRepositoryIntelligenceProvider",
    "RepositoryIntelligenceFreshness",
    "RepositoryIntelligenceOperation",
    "RepositoryIntelligenceProvenance",
    "RepositoryIntelligenceStateClass",
    "RepositorySnapshotLoader",
    "WorkspaceAwareRepositoryIntelligenceProvider",
    "WorkspaceRepositorySnapshot",
    "WorkspaceRepositorySnapshotLoader",
    "repository_intelligence_capability_specs",
]
