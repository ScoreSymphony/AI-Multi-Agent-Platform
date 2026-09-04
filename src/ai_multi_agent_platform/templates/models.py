"""Canonical reusable Template models for issue #78.

Templates contain portable configuration intent. They are not runtime snapshots and
must never become a carrier for plaintext secrets or backend-private session state.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType

from ai_multi_agent_platform.contracts.types import FrozenJsonValue
from ai_multi_agent_platform.domain import OwnerRef, new_id, validate_id


def utc_now() -> datetime:
    return datetime.now(UTC)


def _require_nonblank(value: str, name: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")


def _freeze_value(value: FrozenJsonValue) -> FrozenJsonValue:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    return value


def _freeze_mapping(value: Mapping[str, FrozenJsonValue]) -> Mapping[str, FrozenJsonValue]:
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


def _validate_unique_nonblank(values: tuple[str, ...], name: str) -> None:
    for value in values:
        _require_nonblank(value, name)
    if len(set(values)) != len(values):
        raise ValueError(f"{name} values must be unique")


class TemplateType(StrEnum):
    AGENT = "agent"
    AGENT_TEAM = "agent_team"
    WORKFLOW_PLAN = "workflow_plan"
    PROJECT = "project"
    WORKSPACE_STRUCTURE = "workspace_structure"
    AUTOMATION = "automation"
    MODEL_ROUTING_POLICY = "model_routing_policy"
    CAPABILITY_ASSIGNMENT = "capability_assignment"
    COMPOSITE = "composite"


class TemplateRevisionState(StrEnum):
    DRAFT = "draft"
    PUBLISHED = "published"


class TemplateTrust(StrEnum):
    LOCAL = "local"
    TRUSTED = "trusted"
    UNTRUSTED = "untrusted"


@dataclass(frozen=True, slots=True)
class TemplateRevisionRef:
    template_id: str
    revision: int

    def __post_init__(self) -> None:
        validate_id(self.template_id, "template")
        if self.revision < 1:
            raise ValueError("template revision must be >= 1")


@dataclass(frozen=True, slots=True)
class TemplateDependency:
    template_id: str
    revision: int | None = None
    optional: bool = False

    def __post_init__(self) -> None:
        validate_id(self.template_id, "template")
        if self.revision is not None and self.revision < 1:
            raise ValueError("dependency revision must be >= 1")


@dataclass(frozen=True, slots=True)
class CapabilityRequirement:
    capability_id: str
    optional: bool = False
    version_constraint: str | None = None
    privileged: bool = False

    def __post_init__(self) -> None:
        _require_nonblank(self.capability_id, "capability_id")
        if self.version_constraint is not None:
            _require_nonblank(self.version_constraint, "capability version constraint")


@dataclass(frozen=True, slots=True)
class TemplateRequirements:
    capabilities: tuple[CapabilityRequirement, ...] = ()
    plugin_ids: tuple[str, ...] = ()
    connector_ids: tuple[str, ...] = ()
    model_policy_refs: tuple[str, ...] = ()
    permission_actions: tuple[str, ...] = ()
    workspace_prerequisites: tuple[str, ...] = ()
    placeholders: tuple[str, ...] = ()
    secret_reference_placeholders: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        capability_ids = tuple(item.capability_id for item in self.capabilities)
        if len(set(capability_ids)) != len(capability_ids):
            raise ValueError("capability requirements must use unique capability IDs")
        _validate_unique_nonblank(self.plugin_ids, "plugin ID")
        _validate_unique_nonblank(self.connector_ids, "connector ID")
        _validate_unique_nonblank(self.model_policy_refs, "model policy reference")
        _validate_unique_nonblank(self.permission_actions, "permission action")
        _validate_unique_nonblank(self.workspace_prerequisites, "workspace prerequisite")
        _validate_unique_nonblank(self.placeholders, "configuration placeholder")
        _validate_unique_nonblank(
            self.secret_reference_placeholders,
            "secret-reference placeholder",
        )
        overlap = set(self.placeholders).intersection(self.secret_reference_placeholders)
        if overlap:
            raise ValueError("ordinary and secret-reference placeholders must not overlap")


@dataclass(frozen=True, slots=True)
class TemplateCompatibility:
    platform_version_range: str | None = None
    contract_versions: Mapping[str, str] = field(default_factory=dict)
    orchestrator_agnostic: bool = True
    provider_agnostic: bool = True
    metadata: Mapping[str, FrozenJsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.platform_version_range is not None:
            _require_nonblank(self.platform_version_range, "platform version range")
        versions = dict(self.contract_versions)
        for key, value in versions.items():
            _require_nonblank(key, "contract name")
            _require_nonblank(value, "contract version")
        object.__setattr__(self, "contract_versions", MappingProxyType(versions))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class TemplateProvenance:
    author: str
    source: str
    trust: TemplateTrust = TemplateTrust.LOCAL
    source_template: TemplateRevisionRef | None = None
    metadata: Mapping[str, FrozenJsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_nonblank(self.author, "template author")
        _require_nonblank(self.source, "template source")
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))


@dataclass(frozen=True, slots=True)
class TemplateConfiguration:
    """Portable canonical configuration payload or validated external reference."""

    payload: Mapping[str, FrozenJsonValue] | None = None
    reference: str | None = None

    def __post_init__(self) -> None:
        if (self.payload is None) == (self.reference is None):
            raise ValueError("template configuration requires exactly one of payload or reference")
        if self.reference is not None:
            _require_nonblank(self.reference, "configuration reference")
        if self.payload is not None:
            object.__setattr__(self, "payload", _freeze_mapping(self.payload))


@dataclass(frozen=True, slots=True)
class TemplateContent:
    name: str
    description: str
    template_type: TemplateType
    configuration: TemplateConfiguration
    dependencies: tuple[TemplateDependency, ...] = ()
    requirements: TemplateRequirements = field(default_factory=TemplateRequirements)
    compatibility: TemplateCompatibility = field(default_factory=TemplateCompatibility)
    provenance: TemplateProvenance = field(
        default_factory=lambda: TemplateProvenance(author="local", source="local")
    )
    tags: tuple[str, ...] = ()
    categories: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _require_nonblank(self.name, "template name")
        dependency_keys = tuple((item.template_id, item.revision) for item in self.dependencies)
        if len(set(dependency_keys)) != len(dependency_keys):
            raise ValueError("template dependencies must be unique")
        _validate_unique_nonblank(self.tags, "template tag")
        _validate_unique_nonblank(self.categories, "template category")


@dataclass(frozen=True, slots=True)
class TemplateDefinition:
    template_id: str
    owner_ref: OwnerRef
    current_revision: int
    latest_published_revision: int | None = None
    project_id: str | None = None
    organization_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.template_id, "template")
        if self.current_revision < 1:
            raise ValueError("current_revision must be >= 1")
        if self.latest_published_revision is not None:
            if self.latest_published_revision < 1:
                raise ValueError("latest_published_revision must be >= 1")
            if self.latest_published_revision > self.current_revision:
                raise ValueError("latest published revision cannot exceed current revision")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.organization_id is not None:
            _require_nonblank(self.organization_id, "organization_id")


@dataclass(frozen=True, slots=True)
class TemplateRevision:
    template_id: str
    revision: int
    state: TemplateRevisionState
    owner_ref: OwnerRef
    content: TemplateContent
    project_id: str | None = None
    organization_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.template_id, "template")
        if self.revision < 1:
            raise ValueError("template revision must be >= 1")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.organization_id is not None:
            _require_nonblank(self.organization_id, "organization_id")

    @property
    def ref(self) -> TemplateRevisionRef:
        return TemplateRevisionRef(template_id=self.template_id, revision=self.revision)


@dataclass(frozen=True, slots=True)
class TemplateResourceChange:
    resource_type: str
    action: str
    resource_id: str | None = None
    description: str = ""
    privileged: bool = False

    def __post_init__(self) -> None:
        _require_nonblank(self.resource_type, "resource_type")
        _require_nonblank(self.action, "resource action")
        if self.resource_id is not None:
            _require_nonblank(self.resource_id, "resource_id")


@dataclass(frozen=True, slots=True)
class TemplateResourceRef:
    resource_type: str
    resource_id: str

    def __post_init__(self) -> None:
        _require_nonblank(self.resource_type, "resource_type")
        _require_nonblank(self.resource_id, "resource_id")


@dataclass(frozen=True, slots=True)
class TemplateInstantiationProvenance:
    source: TemplateRevisionRef
    applied_by: OwnerRef
    applied_at: datetime = field(default_factory=utc_now)


@dataclass(frozen=True, slots=True)
class TemplateInstantiation:
    source: TemplateRevisionRef
    applied_by: OwnerRef
    resource_refs: tuple[TemplateResourceRef, ...]
    instance_id: str = field(default_factory=lambda: new_id("template_instance"))
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.instance_id, "template_instance")
