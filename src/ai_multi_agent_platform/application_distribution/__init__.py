"""Application packaging and provider-neutral release distribution."""

from .contracts import (
    ApplicationReleasePublisher,
    ApplicationReleaseRepository,
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
from .repository import InMemoryApplicationReleaseRepository
from .service import ApplicationDistributionService

__all__ = [
    "APPLICATION_RELEASE_SCHEMA_VERSION",
    "ApplicationArtifact",
    "ApplicationDistributionService",
    "ApplicationRelease",
    "ApplicationReleasePublisher",
    "ApplicationReleaseRepository",
    "BuildSpecification",
    "BuildTarget",
    "BuildTargetState",
    "BuildTargetStatus",
    "GateEvidence",
    "GateStatus",
    "GitHubReleasePublisher",
    "InMemoryApplicationReleaseRepository",
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
