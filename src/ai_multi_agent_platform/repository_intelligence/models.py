"""Provider-neutral repository-intelligence result metadata."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.repositories.models import validate_git_revision


class RepositoryIntelligenceStateClass(StrEnum):
    """Trust/persistence classification for provider-owned state."""

    DERIVED_INDEX = "derived_index"
    AUTHORED_METADATA = "authored_metadata"
    TELEMETRY = "telemetry"


class RepositoryIntelligenceFreshness(StrEnum):
    """Normalized freshness evidence for intelligence results."""

    LIVE_REVISION = "live_revision"
    FRESH_INDEX = "fresh_index"
    STALE_INDEX = "stale_index"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class RepositoryIntelligenceProvenance:
    """Exact source/provider evidence attached to every source-derived result."""

    repository_id: str
    requested_revision: str
    resolved_revision: str
    intelligence_provider_id: str
    freshness: RepositoryIntelligenceFreshness

    def __post_init__(self) -> None:
        if not self.repository_id.strip():
            raise ValueError("repository_id must not be blank")
        if not self.requested_revision.strip():
            raise ValueError("requested_revision must not be blank")
        object.__setattr__(
            self,
            "resolved_revision",
            validate_git_revision(self.resolved_revision),
        )
        if not self.intelligence_provider_id.strip():
            raise ValueError("intelligence_provider_id must not be blank")

    def to_dict(self) -> dict[str, JsonValue]:
        return {
            "repository_id": self.repository_id,
            "requested_revision": self.requested_revision,
            "resolved_revision": self.resolved_revision,
            "intelligence_provider_id": self.intelligence_provider_id,
            "freshness": self.freshness.value,
        }
