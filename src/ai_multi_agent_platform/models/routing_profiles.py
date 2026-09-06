"""Canonical durable model-routing profile domain for issue #309."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from ai_multi_agent_platform.domain import OwnerRef, Provenance, new_id, validate_id

from .types import RoutingRequirements

MODEL_ROUTING_PROFILE_SCHEMA_VERSION = "1.0"
_MODEL_ROUTING_PROFILE_ID_PREFIX = "model_routing_profile"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _require_nonblank(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


def _require_supported_schema_version(value: str) -> None:
    _require_nonblank(value, "routing profile schema_version")
    if value != MODEL_ROUTING_PROFILE_SCHEMA_VERSION:
        raise ValueError(
            "unsupported routing profile schema_version: "
            f"{value!r}; expected {MODEL_ROUTING_PROFILE_SCHEMA_VERSION!r}"
        )


def new_model_routing_profile_id() -> str:
    """Create one stable canonical routing-profile identity."""

    return new_id(_MODEL_ROUTING_PROFILE_ID_PREFIX)


class RoutingProfileFallbackPolicy(StrEnum):
    """Deterministic behavior when an explicitly preferred model cannot be used."""

    FAIL = "fail"
    ROUTE = "route"


@dataclass(frozen=True, slots=True)
class ModelRoutingProfilePolicy:
    """Provider-neutral routing intent stored in an immutable profile revision."""

    requirements: RoutingRequirements = field(default_factory=RoutingRequirements)
    preferred_model_ids: tuple[str, ...] = ()
    fallback: RoutingProfileFallbackPolicy = RoutingProfileFallbackPolicy.ROUTE

    def __post_init__(self) -> None:
        for model_id in self.preferred_model_ids:
            _require_nonblank(model_id, "preferred model configuration ID")
        if len(set(self.preferred_model_ids)) != len(self.preferred_model_ids):
            raise ValueError("preferred model configuration IDs must be unique")


@dataclass(frozen=True, slots=True)
class ModelRoutingProfileDefinition:
    """Stable profile identity pointing at the current immutable revision."""

    profile_id: str
    owner_ref: OwnerRef
    current_revision: int
    project_id: str | None = None
    enabled: bool = True
    created_at: datetime = field(default_factory=_utc_now)
    updated_at: datetime = field(default_factory=_utc_now)
    schema_version: str = MODEL_ROUTING_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_id(self.profile_id, _MODEL_ROUTING_PROFILE_ID_PREFIX)
        if self.current_revision < 1:
            raise ValueError("routing profile current_revision must be >= 1")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.updated_at < self.created_at:
            raise ValueError("routing profile updated_at cannot precede created_at")
        _require_supported_schema_version(self.schema_version)


@dataclass(frozen=True, slots=True)
class ModelRoutingProfileRevision:
    """One immutable version of reusable model-routing configuration."""

    profile_id: str
    revision: int
    name: str
    owner_ref: OwnerRef
    policy: ModelRoutingProfilePolicy = field(default_factory=ModelRoutingProfilePolicy)
    description: str = ""
    project_id: str | None = None
    provenance: Provenance | None = None
    created_at: datetime = field(default_factory=_utc_now)
    schema_version: str = MODEL_ROUTING_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        validate_id(self.profile_id, _MODEL_ROUTING_PROFILE_ID_PREFIX)
        if self.revision < 1:
            raise ValueError("routing profile revision must be >= 1")
        _require_nonblank(self.name, "routing profile name")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        _require_supported_schema_version(self.schema_version)

    @property
    def ref(self) -> ModelRoutingProfileRef:
        return ModelRoutingProfileRef(self.profile_id, self.revision)


@dataclass(frozen=True, slots=True)
class ModelRoutingProfileRef:
    """Exact immutable routing-profile revision reference."""

    profile_id: str
    revision: int

    def __post_init__(self) -> None:
        validate_id(self.profile_id, _MODEL_ROUTING_PROFILE_ID_PREFIX)
        if self.revision < 1:
            raise ValueError("routing profile revision reference must be >= 1")

    @property
    def canonical_ref(self) -> str:
        return f"{self.profile_id}@r{self.revision}"

    @classmethod
    def parse(cls, value: str) -> ModelRoutingProfileRef:
        _require_nonblank(value, "routing profile reference")
        profile_id, separator, raw_revision = value.rpartition("@r")
        if not separator or not raw_revision.isdigit():
            raise ValueError("routing profile reference must use '<profile_id>@r<revision>'")
        return cls(profile_id=profile_id, revision=int(raw_revision))
