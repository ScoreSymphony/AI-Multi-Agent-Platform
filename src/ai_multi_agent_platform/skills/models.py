"""Canonical provider-neutral Skill resources and immutable Skill Bundle evidence.

A Skill describes *how* a class of work should be performed. It is deliberately
separate from executable Capabilities/Tools, Agent identity, plugin packaging and
provider-private prompt/session formats.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, Provenance, new_id, validate_id
from ai_multi_agent_platform.models import RoutingRequirements


def utc_now() -> datetime:
    return datetime.now(UTC)


def _require_nonblank(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


def _freeze_mapping(value: Mapping[str, JsonValue]) -> Mapping[str, JsonValue]:
    return MappingProxyType(dict(value))


def _freeze_string_mapping(value: Mapping[str, str], name: str) -> Mapping[str, str]:
    frozen = dict(value)
    for key, item in frozen.items():
        _require_nonblank(key, f"{name} key")
        _require_nonblank(item, f"{name} value")
    return MappingProxyType(frozen)


def _validate_optional_id(value: str | None, prefix: str) -> None:
    if value is not None:
        validate_id(value, prefix)


class SkillTrustStatus(StrEnum):
    """Trust lifecycle for reusable methods, especially third-party content."""

    DISCOVERED = "discovered"
    SOURCE_VERIFIED = "source_verified"
    REVIEWED = "reviewed"
    PILOT = "pilot"
    ADOPTED = "adopted"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class SkillEvaluationStatus(StrEnum):
    NOT_EVALUATED = "not_evaluated"
    PASSED = "passed"
    FAILED = "failed"
    DEFERRED = "deferred"


class SkillRiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class SkillContent:
    """Versioned method content or a provider-neutral immutable content reference."""

    content: str | None = None
    ref: str | None = None
    version: str | None = None

    def __post_init__(self) -> None:
        if (self.content is None) == (self.ref is None):
            raise ValueError("skill content requires exactly one of content or ref")
        if self.content is not None:
            _require_nonblank(self.content, "skill content")
        if self.ref is not None:
            _require_nonblank(self.ref, "skill content ref")
        if self.version is not None:
            _require_nonblank(self.version, "skill content version")


@dataclass(frozen=True, slots=True)
class SkillCapabilityRequirement:
    """Semantic dependency on a canonical #12 Capability, never on a private tool name."""

    capability_id: str
    exact_version: str | None = None
    minimum_version: str | None = None
    maximum_version: str | None = None
    required_features: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_nonblank(self.capability_id, "capability_id")
        for value, name in (
            (self.exact_version, "exact_version"),
            (self.minimum_version, "minimum_version"),
            (self.maximum_version, "maximum_version"),
        ):
            if value is not None:
                _require_nonblank(value, name)
        if self.exact_version is not None and (
            self.minimum_version is not None
            or self.maximum_version is not None
            or self.required_features
        ):
            raise ValueError("exact capability version cannot be combined with compatibility rules")
        for feature in self.required_features:
            _require_nonblank(feature, "required capability feature")
        if len(set(self.required_features)) != len(self.required_features):
            raise ValueError("required capability features must be unique")


@dataclass(frozen=True, slots=True)
class SkillSource:
    """Third-party source, review surface and cost/security provenance."""

    source_url: str
    source_revision: str
    license: str
    checksum: str | None = None
    signature: str | None = None
    requested_capability_ids: tuple[str, ...] = ()
    filesystem_implications: tuple[str, ...] = ()
    network_implications: tuple[str, ...] = ()
    embedded_hook_refs: tuple[str, ...] = ()
    cost_implications: str | None = None

    def __post_init__(self) -> None:
        _require_nonblank(self.source_url, "skill source URL")
        _require_nonblank(self.source_revision, "skill source revision")
        _require_nonblank(self.license, "skill license")
        for value, name in (
            (self.checksum, "skill checksum"),
            (self.signature, "skill signature"),
            (self.cost_implications, "skill cost implications"),
        ):
            if value is not None:
                _require_nonblank(value, name)
        for capability_id in self.requested_capability_ids:
            _require_nonblank(capability_id, "requested capability ID")
        for item in (
            *self.filesystem_implications,
            *self.network_implications,
            *self.embedded_hook_refs,
        ):
            _require_nonblank(item, "skill source implication/reference")


@dataclass(frozen=True, slots=True)
class SkillRevisionRef:
    skill_id: str
    revision: int

    def __post_init__(self) -> None:
        validate_id(self.skill_id, "skill")
        if self.revision < 1:
            raise ValueError("skill revision reference must be >= 1")


@dataclass(frozen=True, slots=True)
class SkillProfile:
    name: str
    purpose_categories: tuple[str, ...]
    content: SkillContent
    description: str = ""
    capability_requirements: tuple[SkillCapabilityRequirement, ...] = ()
    compatible_agent_roles: tuple[str, ...] = ()
    routing_requirements: RoutingRequirements = field(default_factory=RoutingRequirements)
    conflicts_with_skill_ids: tuple[str, ...] = ()
    risk_level: SkillRiskLevel = SkillRiskLevel.LOW
    trust_status: SkillTrustStatus = SkillTrustStatus.ADOPTED
    evaluation_status: SkillEvaluationStatus = SkillEvaluationStatus.NOT_EVALUATED
    source: SkillSource | None = None
    enabled: bool = True
    deprecated: bool = False
    replacement: SkillRevisionRef | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonblank(self.name, "skill name")
        if not self.purpose_categories:
            raise ValueError("skill requires at least one purpose category")
        for category in self.purpose_categories:
            _require_nonblank(category, "skill purpose category")
        if len(set(self.purpose_categories)) != len(self.purpose_categories):
            raise ValueError("skill purpose categories must be unique")
        requirement_ids = [item.capability_id for item in self.capability_requirements]
        if len(set(requirement_ids)) != len(requirement_ids):
            raise ValueError("skill capability requirements must use unique capability IDs")
        for role in self.compatible_agent_roles:
            _require_nonblank(role, "compatible agent role")
        if len(set(self.compatible_agent_roles)) != len(self.compatible_agent_roles):
            raise ValueError("compatible agent roles must be unique")
        for skill_id in self.conflicts_with_skill_ids:
            validate_id(skill_id, "skill")
        if len(set(self.conflicts_with_skill_ids)) != len(self.conflicts_with_skill_ids):
            raise ValueError("conflicting skill IDs must be unique")
        if self.replacement is not None and not self.deprecated:
            raise ValueError("replacement metadata requires deprecated=True")
        if self.source is not None:
            requested = set(self.source.requested_capability_ids)
            declared = set(requirement_ids)
            if not requested.issubset(declared):
                raise ValueError(
                    "third-party requested capabilities must be declared capability requirements"
                )
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class SkillDefinition:
    """Stable Skill identity pointing to the latest immutable revision."""

    skill_id: str
    owner_ref: OwnerRef
    current_revision: int
    project_id: str | None = None
    workspace_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.skill_id, "skill")
        if self.current_revision < 1:
            raise ValueError("skill current_revision must be >= 1")
        _validate_optional_id(self.project_id, "project")
        _validate_optional_id(self.workspace_id, "workspace")
        if self.updated_at < self.created_at:
            raise ValueError("skill updated_at cannot precede created_at")


@dataclass(frozen=True, slots=True)
class SkillRevision:
    skill_id: str
    revision: int
    profile: SkillProfile
    owner_ref: OwnerRef
    project_id: str | None = None
    workspace_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    provenance: Provenance | None = None

    def __post_init__(self) -> None:
        validate_id(self.skill_id, "skill")
        if self.revision < 1:
            raise ValueError("skill revision must be >= 1")
        _validate_optional_id(self.project_id, "project")
        _validate_optional_id(self.workspace_id, "workspace")

    @property
    def ref(self) -> SkillRevisionRef:
        return SkillRevisionRef(self.skill_id, self.revision)


@dataclass(frozen=True, slots=True)
class SkillBundleEntry:
    ref: SkillRevisionRef
    content_digest: str
    trust_status: SkillTrustStatus
    source_revision: str | None = None
    source_checksum: str | None = None

    def __post_init__(self) -> None:
        _validate_digest(self.content_digest, "skill content digest")
        for value, name in (
            (self.source_revision, "skill source revision"),
            (self.source_checksum, "skill source checksum"),
        ):
            if value is not None:
                _require_nonblank(value, name)


@dataclass(frozen=True, slots=True)
class SkillBundle:
    """Immutable evidence of the exact method set resolved for one execution context."""

    skill_bundle_id: str
    digest: str
    entries: tuple[SkillBundleEntry, ...]
    resolver_version: str
    policy_version: str
    run_id: str
    task_id: str
    agent_id: str
    agent_revision: int
    step_id: str | None = None
    project_id: str | None = None
    workspace_id: str | None = None
    capability_ids: tuple[str, ...] = ()
    capability_versions: Mapping[str, str] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    audit_metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        validate_id(self.skill_bundle_id, "skill_bundle")
        _validate_digest(self.digest, "skill bundle digest")
        validate_id(self.run_id, "run")
        validate_id(self.task_id, "task")
        validate_id(self.agent_id, "agent")
        if self.agent_revision < 1:
            raise ValueError("agent_revision must be >= 1")
        if self.step_id is not None:
            validate_id(self.step_id, "step")
        _validate_optional_id(self.project_id, "project")
        _validate_optional_id(self.workspace_id, "workspace")
        _require_nonblank(self.resolver_version, "resolver version")
        _require_nonblank(self.policy_version, "resolver policy version")
        refs = [(entry.ref.skill_id, entry.ref.revision) for entry in self.entries]
        if len(set(refs)) != len(refs):
            raise ValueError("skill bundle entries must be unique")
        if len(set(self.capability_ids)) != len(self.capability_ids):
            raise ValueError("skill bundle capability IDs must be unique")
        if set(self.capability_versions) - set(self.capability_ids):
            raise ValueError("capability versions must refer to bundle capability IDs")
        object.__setattr__(
            self,
            "capability_versions",
            _freeze_string_mapping(self.capability_versions, "capability version"),
        )
        object.__setattr__(self, "audit_metadata", _freeze_mapping(self.audit_metadata))


@dataclass(frozen=True, slots=True)
class SkillRunBinding:
    """Immutable Run/Agent evidence linking execution to one exact Skill Bundle."""

    binding_id: str
    run_id: str
    task_id: str
    agent_id: str
    agent_revision: int
    skill_bundle_id: str
    skill_bundle_hash: str
    agent_run_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.binding_id, "skill_binding")
        validate_id(self.run_id, "run")
        validate_id(self.task_id, "task")
        validate_id(self.agent_id, "agent")
        if self.agent_revision < 1:
            raise ValueError("agent_revision must be >= 1")
        validate_id(self.skill_bundle_id, "skill_bundle")
        _validate_digest(self.skill_bundle_hash, "skill bundle hash")
        if self.agent_run_id is not None:
            validate_id(self.agent_run_id, "agent_run")


def _validate_digest(value: str, name: str) -> None:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 hex digest")


def new_skill_id() -> str:
    return new_id("skill")


def new_skill_bundle_id() -> str:
    return new_id("skill_bundle")


def new_skill_binding_id() -> str:
    return new_id("skill_binding")
