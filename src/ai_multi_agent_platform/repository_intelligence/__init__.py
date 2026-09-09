"""Required provider-neutral repository/code-intelligence core and optional enhancements."""

from .baseline import BaselineRepositoryIntelligenceProvider, RepositorySnapshotLoader
from .capabilities import (
    RepositoryIntelligenceOperation,
    repository_intelligence_capability_specs,
)
from .context_funnel import (
    RepositoryContextCaller,
    RepositoryContextFunnel,
    RepositoryContextFunnelReport,
    RepositoryContextRequest,
    RepositoryContextRequestResolver,
    RepositoryIntelligenceContextSourceAdapter,
)
from .evaluation_matrix import (
    RepositoryIntelligenceComparison,
    RepositoryIntelligenceEvaluationObservation,
    RepositoryIntelligenceMetric,
    RepositoryIntelligenceMetricDelta,
    compare_repository_intelligence_observations,
)
from .fallback import (
    CapabilityInvocationPort,
    RepositoryIntelligenceFallbackInvoker,
    RepositoryIntelligenceFallbackResultPort,
    RepositoryIntelligenceInvocationOutcome,
)
from .models import (
    RepositoryIntelligenceFreshness,
    RepositoryIntelligenceProvenance,
    RepositoryIntelligenceStateClass,
)
from .projectatlas import (
    PROJECTATLAS_ARCHIVE_SHA256,
    PROJECTATLAS_EXTENSION_ID,
    PROJECTATLAS_PLUGIN_ID,
    PROJECTATLAS_PLUGIN_VERSION,
    PROJECTATLAS_PROVIDER_ID,
    PROJECTATLAS_RUNTIME_VERSION,
    LocalProjectAtlasRuntimeProbe,
    ProjectAtlasCandidatePlugin,
    ProjectAtlasRuntimeIdentity,
    ProjectAtlasRuntimeProbe,
    projectatlas_candidate_manifest,
)
from .projectatlas_adapter import (
    ProjectAtlasCommandRequest,
    ProjectAtlasCommandResult,
    ProjectAtlasCommandRunner,
    ProjectAtlasContainmentEvidence,
    ProjectAtlasSourceBinding,
    ProjectAtlasSourceBindingResolver,
    ProjectAtlasV045Normalizer,
)
from .projectatlas_resources import (
    PROJECTATLAS_PILOT_EVIDENCE_REF,
    PROJECTATLAS_PILOT_PEAK_RSS_BYTES,
    PROJECTATLAS_PILOT_STATE_BYTES,
    projectatlas_resource_profile,
    with_projectatlas_admission_bounds,
)
from .resources import (
    RepositoryIntelligenceResourceEnvelope,
    RepositoryIntelligenceResourceProfile,
    RepositoryIntelligenceWorkload,
    ResourceEvidenceStatus,
)
from .search_bridge import (
    RepositoryIntelligenceSearchFederator,
    RepositorySearchAuthorizer,
    RepositorySearchCaller,
    RepositorySearchScope,
)
from .workspace import (
    WorkspaceAwareRepositoryIntelligenceProvider,
    WorkspaceRepositorySnapshot,
    WorkspaceRepositorySnapshotLoader,
)

__all__ = [
    "PROJECTATLAS_ARCHIVE_SHA256",
    "PROJECTATLAS_EXTENSION_ID",
    "PROJECTATLAS_PILOT_EVIDENCE_REF",
    "PROJECTATLAS_PILOT_PEAK_RSS_BYTES",
    "PROJECTATLAS_PILOT_STATE_BYTES",
    "PROJECTATLAS_PLUGIN_ID",
    "PROJECTATLAS_PLUGIN_VERSION",
    "PROJECTATLAS_PROVIDER_ID",
    "PROJECTATLAS_RUNTIME_VERSION",
    "BaselineRepositoryIntelligenceProvider",
    "CapabilityInvocationPort",
    "LocalProjectAtlasRuntimeProbe",
    "ProjectAtlasCandidatePlugin",
    "ProjectAtlasCommandRequest",
    "ProjectAtlasCommandResult",
    "ProjectAtlasCommandRunner",
    "ProjectAtlasContainmentEvidence",
    "ProjectAtlasRuntimeIdentity",
    "ProjectAtlasRuntimeProbe",
    "ProjectAtlasSourceBinding",
    "ProjectAtlasSourceBindingResolver",
    "ProjectAtlasV045Normalizer",
    "RepositoryContextCaller",
    "RepositoryContextFunnel",
    "RepositoryContextFunnelReport",
    "RepositoryContextRequest",
    "RepositoryContextRequestResolver",
    "RepositoryIntelligenceComparison",
    "RepositoryIntelligenceContextSourceAdapter",
    "RepositoryIntelligenceEvaluationObservation",
    "RepositoryIntelligenceFallbackInvoker",
    "RepositoryIntelligenceFallbackResultPort",
    "RepositoryIntelligenceFreshness",
    "RepositoryIntelligenceInvocationOutcome",
    "RepositoryIntelligenceMetric",
    "RepositoryIntelligenceMetricDelta",
    "RepositoryIntelligenceOperation",
    "RepositoryIntelligenceProvenance",
    "RepositoryIntelligenceResourceEnvelope",
    "RepositoryIntelligenceResourceProfile",
    "RepositoryIntelligenceSearchFederator",
    "RepositoryIntelligenceStateClass",
    "RepositoryIntelligenceWorkload",
    "RepositorySearchAuthorizer",
    "RepositorySearchCaller",
    "RepositorySearchScope",
    "RepositorySnapshotLoader",
    "ResourceEvidenceStatus",
    "WorkspaceAwareRepositoryIntelligenceProvider",
    "WorkspaceRepositorySnapshot",
    "WorkspaceRepositorySnapshotLoader",
    "compare_repository_intelligence_observations",
    "projectatlas_candidate_manifest",
    "projectatlas_resource_profile",
    "repository_intelligence_capability_specs",
    "with_projectatlas_admission_bounds",
]
