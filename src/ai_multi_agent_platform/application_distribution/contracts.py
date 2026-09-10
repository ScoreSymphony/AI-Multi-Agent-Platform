"""Replaceable persistence, placement and publication boundaries for application releases."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Protocol

from ai_multi_agent_platform.contracts.types import JsonValue, OperationContext
from ai_multi_agent_platform.security import ActorIdentity, redact_sensitive

from .models import ApplicationRelease, BuildSpecification, BuildTarget, ReleaseVisibility


class ApplicationReleaseRepository(Protocol):
    @abstractmethod
    async def get(self, release_id: str) -> ApplicationRelease: ...

    @abstractmethod
    async def list(self) -> tuple[ApplicationRelease, ...]: ...

    @abstractmethod
    async def save(
        self,
        release: ApplicationRelease,
        *,
        expected_revision: int | None,
    ) -> ApplicationRelease: ...

    @abstractmethod
    async def find_version(
        self,
        application_id: str,
        version: str,
        channel: str,
    ) -> ApplicationRelease | None: ...

    @abstractmethod
    async def find_run(self, run_id: str) -> ApplicationRelease | None: ...


class BuildTargetMatcher(Protocol):
    """Provider-neutral check for whether a build target has an eligible execution host."""

    @abstractmethod
    async def supports(
        self,
        specification: BuildSpecification,
        target: BuildTarget,
    ) -> bool: ...


@dataclass(frozen=True, slots=True)
class PublishContext:
    actor: ActorIdentity
    operation: OperationContext
    approval_id: str | None = None
    configuration: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        configuration = dict(self.configuration)
        if redact_sensitive(configuration) != configuration:
            raise ValueError(
                "publisher configuration must contain references only, not embedded credentials"
            )
        object.__setattr__(self, "configuration", MappingProxyType(configuration))


@dataclass(frozen=True, slots=True)
class PublishedArtifact:
    artifact_id: str
    download_url: str
    external_metadata: dict[str, JsonValue] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class PublicationResult:
    provider_id: str
    release_url: str
    visibility: ReleaseVisibility
    artifacts: tuple[PublishedArtifact, ...]
    latest_url: str | None = None
    external_metadata: dict[str, JsonValue] = field(default_factory=dict)


class ApplicationReleasePublisher(Protocol):
    """Replaceable publication boundary.

    Implementations must make retries idempotent for the same canonical release/artifact IDs or
    fail with an explicit conflict. They must never silently overwrite a different external
    release or asset.
    """

    @property
    @abstractmethod
    def provider_id(self) -> str: ...

    @abstractmethod
    async def preview(
        self,
        release: ApplicationRelease,
        manifest: dict[str, JsonValue],
        context: PublishContext,
    ) -> dict[str, JsonValue]: ...

    @abstractmethod
    async def publish(
        self,
        release: ApplicationRelease,
        manifest: dict[str, JsonValue],
        context: PublishContext,
    ) -> PublicationResult: ...
