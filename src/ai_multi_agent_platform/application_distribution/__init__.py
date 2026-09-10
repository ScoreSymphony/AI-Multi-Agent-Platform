"""Application packaging and provider-neutral release distribution."""

from .contracts import (
    ApplicationReleasePublisher,
    ApplicationReleaseRepository,
    BuildTargetMatcher,
    PublicationResult,
    PublishedArtifact,
    PublishContext,
)
from .github import GitHubReleasePublisher
from .manifest import canonical_manifest_bytes, manifest_sha256, release_manifest
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
from .placement import DistributedBuildTargetMatcher
from .repository import (
    APPLICATION_RELEASE_REPOSITORY_SCHEMA_VERSION,
    InMemoryApplicationReleaseRepository,
    JsonApplicationReleaseRepository,
)
from .service import ApplicationDistributionService

__all__ = [
    "APPLICATION_RELEASE_REPOSITORY_SCHEMA_VERSION",
    "APPLICATION_RELEASE_SCHEMA_VERSION",
    "ApplicationArtifact",
    "ApplicationDistributionService",
    "ApplicationRelease",
    "ApplicationReleasePublisher",
    "ApplicationReleaseRepository",
    "BuildSpecification",
    "BuildTarget",
    "BuildTargetMatcher",
    "BuildTargetState",
    "BuildTargetStatus",
    "DistributedBuildTargetMatcher",
    "GateEvidence",
    "GateStatus",
    "GitHubReleasePublisher",
    "InMemoryApplicationReleaseRepository",
    "JsonApplicationReleaseRepository",
    "PackageType",
    "PublicationResult",
    "PublishedArtifact",
    "PublishContext",
    "ReleaseChannel",
    "ReleaseStatus",
    "ReleaseVisibility",
    "canonical_manifest_bytes",
    "manifest_sha256",
    "release_manifest",
]
