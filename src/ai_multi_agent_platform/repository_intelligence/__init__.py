"""Optional provider-neutral repository/code-intelligence integration."""

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

__all__ = [
    "BaselineRepositoryIntelligenceProvider",
    "RepositoryIntelligenceFreshness",
    "RepositoryIntelligenceOperation",
    "RepositoryIntelligenceProvenance",
    "RepositoryIntelligenceStateClass",
    "RepositorySnapshotLoader",
    "repository_intelligence_capability_specs",
]
