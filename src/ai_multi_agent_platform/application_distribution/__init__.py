"""Application packaging and provider-neutral release distribution."""

from .build_provenance import ApplicationBuildLifecycleBackend
from .contracts import (
    ApplicationReleasePublisher,
    ApplicationReleaseRepository,
    BuildTargetMatcher,
    PublicationResult,
    PublishContext,
    PublishedArtifact,
)
from .execution import APPLICATION_BUILD_ACTION, ApplicationCommandExecutor
from .gate_provenance import ApplicationReleaseGateCoordinator
from .gates import (
    DeterministicGateCheck,
    ReleaseGateKind,
    ReleaseGatePolicy,
    ReleaseGateRequirement,
    StaticReleaseGatePolicy,
    artifact_subject_revision,
    bind_gate_to_release,
    gate_is_current,
    publication_readiness,
    release_subject_digest,
    required_gate_names,
    verification_subject,
)
from .github import GitHubReleasePublisher
from .manifest import (
    APPLICATION_RELEASE_MANIFEST_SCHEMA,
    MANIFEST_SCHEMA_VERSION,
    canonical_manifest_bytes,
    manifest_sha256,
    manifest_validation_errors,
    release_manifest,
)
from .models import (
    APPLICATION_RELEASE_SCHEMA_VERSION,
    ApplicationArtifact,
    ApplicationRelease,
    BuildSpecification,
    BuildTarget,
    BuildTargetState,
    BuildTargetStatus,
    GateEvidence,
    GateStatus,
    PackageType,
    ReleaseChannel,
    ReleaseStatus,
    ReleaseVisibility,
)
from .placement import DistributedBuildTargetMatcher, LocalBuildTargetMatcher
from .provenance_service import ApplicationDistributionService
from .repository import (
    APPLICATION_RELEASE_REPOSITORY_SCHEMA_VERSION,
    InMemoryApplicationReleaseRepository,
    JsonApplicationReleaseRepository,
)

__all__ = [
    "APPLICATION_BUILD_ACTION",
    "APPLICATION_RELEASE_MANIFEST_SCHEMA",
    "APPLICATION_RELEASE_REPOSITORY_SCHEMA_VERSION",
    "APPLICATION_RELEASE_SCHEMA_VERSION",
    "MANIFEST_SCHEMA_VERSION",
    "ApplicationArtifact",
    "ApplicationBuildLifecycleBackend",
    "ApplicationCommandExecutor",
    "ApplicationDistributionService",
    "ApplicationRelease",
    "ApplicationReleaseGateCoordinator",
    "ApplicationReleasePublisher",
    "ApplicationReleaseRepository",
    "BuildSpecification",
    "BuildTarget",
    "BuildTargetMatcher",
    "BuildTargetState",
    "BuildTargetStatus",
    "DeterministicGateCheck",
    "DistributedBuildTargetMatcher",
    "GateEvidence",
    "GateStatus",
    "GitHubReleasePublisher",
    "InMemoryApplicationReleaseRepository",
    "JsonApplicationReleaseRepository",
    "LocalBuildTargetMatcher",
    "PackageType",
    "PublicationResult",
    "PublishedArtifact",
    "PublishContext",
    "ReleaseChannel",
    "ReleaseGateKind",
    "ReleaseGatePolicy",
    "ReleaseGateRequirement",
    "ReleaseStatus",
    "ReleaseVisibility",
    "StaticReleaseGatePolicy",
    "artifact_subject_revision",
    "bind_gate_to_release",
    "canonical_manifest_bytes",
    "gate_is_current",
    "manifest_sha256",
    "manifest_validation_errors",
    "publication_readiness",
    "release_manifest",
    "release_subject_digest",
    "required_gate_names",
    "verification_subject",
]
