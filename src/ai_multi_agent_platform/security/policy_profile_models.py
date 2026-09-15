"""Canonical authorization policy-profile data model and validation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts import OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id, validate_id

from .authorization import ActorType, AuthorizationAction, ResourceType

POLICY_PROFILE_SCHEMA_VERSION = "1"


def utc_now() -> datetime:
    return datetime.now(UTC)


def _non_blank(value: str, name: str) -> str:
    if not value.strip():
        raise ValueError(f"{name} must not be blank")
    return value


def _unique_nonblank(values: tuple[str, ...], name: str) -> tuple[str, ...]:
    if any(not value.strip() for value in values):
        raise ValueError(f"{name} must not contain blank values")
    if len(values) != len(set(values)):
        raise ValueError(f"{name} must not contain duplicates")
    return tuple(values)


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value


def _require_within_outer_scope(
    outer_id: str | None,
    values: tuple[str, ...],
    scope_name: str,
) -> None:
    if outer_id is not None and any(value != outer_id for value in values):
        raise ValueError(f"{scope_name} contains value outside profile outer {scope_name}")


@dataclass(frozen=True, slots=True)
class AuthorizationPolicyProfileRef:
    policy_profile_id: str
    revision: int

    def __post_init__(self) -> None:
        validate_id(self.policy_profile_id, "authorization_policy_profile")
        if self.revision < 1:
            raise ValueError("policy profile revision must be >= 1")

    @property
    def token(self) -> str:
        """Stable textual exact-revision reference for other canonical resources."""

        return f"{self.policy_profile_id}@{self.revision}"


@dataclass(frozen=True, slots=True)
class AuthorizationPolicyScopeConstraints:
    """Provider-neutral canonical scope restrictions carried by one profile revision."""

    project_ids: tuple[str, ...] = ()
    organization_ids: tuple[str, ...] = ()
    team_ids: tuple[str, ...] = ()
    workspace_ids: tuple[str, ...] = ()
    resource_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        projects = _unique_nonblank(self.project_ids, "project_ids")
        organizations = _unique_nonblank(self.organization_ids, "organization_ids")
        teams = _unique_nonblank(self.team_ids, "team_ids")
        workspaces = _unique_nonblank(self.workspace_ids, "workspace_ids")
        resources = _unique_nonblank(self.resource_ids, "resource_ids")
        for value in projects:
            validate_id(value, "project")
        for value in organizations:
            validate_id(value, "organization")
        for value in teams:
            validate_id(value, "team")
        for value in workspaces:
            validate_id(value, "workspace")
        object.__setattr__(self, "project_ids", projects)
        object.__setattr__(self, "organization_ids", organizations)
        object.__setattr__(self, "team_ids", teams)
        object.__setattr__(self, "workspace_ids", workspaces)
        object.__setattr__(self, "resource_ids", resources)


@dataclass(frozen=True, slots=True)
class AuthorizationPolicyConditions:
    """Small canonical condition vocabulary; never contains provider-native policy syntax."""

    required_security_labels: tuple[str, ...] = ()
    allowed_node_ids: tuple[str, ...] = ()
    allowed_side_effects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        labels = _unique_nonblank(self.required_security_labels, "required_security_labels")
        nodes = _unique_nonblank(self.allowed_node_ids, "allowed_node_ids")
        side_effects = _unique_nonblank(self.allowed_side_effects, "allowed_side_effects")
        for node_id in nodes:
            validate_id(node_id, "node")
        object.__setattr__(self, "required_security_labels", labels)
        object.__setattr__(self, "allowed_node_ids", nodes)
        object.__setattr__(self, "allowed_side_effects", side_effects)


@dataclass(frozen=True, slots=True)
class AuthorizationPolicyProvenance:
    created_by: str
    source: str
    source_reference: str | None = None
    imported: bool = False
    trusted: bool = True

    def __post_init__(self) -> None:
        _non_blank(self.created_by, "policy provenance created_by")
        _non_blank(self.source, "policy provenance source")
        if self.source_reference is not None:
            _non_blank(self.source_reference, "policy provenance source_reference")
        if self.imported and self.source == "local":
            raise ValueError("imported policy provenance must identify a non-local source")


@dataclass(frozen=True, slots=True)
class AuthorizationPolicyProfileContent:
    name: str
    description: str = ""
    allowed_actions: tuple[AuthorizationAction, ...] = ()
    approval_required_actions: tuple[AuthorizationAction, ...] = ()
    resource_types: tuple[ResourceType, ...] = ()
    scope_constraints: AuthorizationPolicyScopeConstraints = field(
        default_factory=AuthorizationPolicyScopeConstraints
    )
    conditions: AuthorizationPolicyConditions = field(default_factory=AuthorizationPolicyConditions)
    provenance: AuthorizationPolicyProvenance = field(
        default_factory=lambda: AuthorizationPolicyProvenance(
            created_by="service:local",
            source="local",
        )
    )
    schema_version: str = POLICY_PROFILE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        _non_blank(self.name, "policy profile name")
        _non_blank(self.schema_version, "policy profile schema_version")
        allowed = tuple(self.allowed_actions)
        approval = tuple(self.approval_required_actions)
        resources = tuple(self.resource_types)
        if len(allowed) != len(set(allowed)):
            raise ValueError("allowed_actions must not contain duplicates")
        if len(approval) != len(set(approval)):
            raise ValueError("approval_required_actions must not contain duplicates")
        if len(resources) != len(set(resources)):
            raise ValueError("resource_types must not contain duplicates")
        if set(allowed).intersection(approval):
            raise ValueError("an action cannot be both directly allowed and approval-required")
        if not (allowed or approval):
            raise ValueError("policy profile must allow or approval-gate at least one action")
        object.__setattr__(self, "allowed_actions", allowed)
        object.__setattr__(self, "approval_required_actions", approval)
        object.__setattr__(self, "resource_types", resources)


@dataclass(frozen=True, slots=True)
class AuthorizationPolicyProfileDefinition:
    policy_profile_id: str
    owner_ref: OwnerRef
    current_revision: int
    enabled: bool = True
    project_id: str | None = None
    organization_id: str | None = None
    team_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.policy_profile_id, "authorization_policy_profile")
        if self.current_revision < 1:
            raise ValueError("current policy profile revision must be >= 1")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.organization_id is not None:
            validate_id(self.organization_id, "organization")
        if self.team_id is not None:
            validate_id(self.team_id, "team")
            if self.organization_id is None:
                raise ValueError("team-scoped policy profiles require organization_id")
        _aware(self.created_at, "created_at")
        _aware(self.updated_at, "updated_at")
        if self.updated_at < self.created_at:
            raise ValueError("updated_at must not precede created_at")


@dataclass(frozen=True, slots=True)
class AuthorizationPolicyProfileRevision:
    policy_profile_id: str
    revision: int
    owner_ref: OwnerRef
    content: AuthorizationPolicyProfileContent
    project_id: str | None = None
    organization_id: str | None = None
    team_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.policy_profile_id, "authorization_policy_profile")
        if self.revision < 1:
            raise ValueError("policy profile revision must be >= 1")
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.organization_id is not None:
            validate_id(self.organization_id, "organization")
        if self.team_id is not None:
            validate_id(self.team_id, "team")
            if self.organization_id is None:
                raise ValueError("team-scoped policy revisions require organization_id")
        scope = self.content.scope_constraints
        _require_within_outer_scope(self.project_id, scope.project_ids, "project scope")
        _require_within_outer_scope(
            self.organization_id,
            scope.organization_ids,
            "organization scope",
        )
        _require_within_outer_scope(self.team_id, scope.team_ids, "team scope")
        _aware(self.created_at, "created_at")

    @property
    def ref(self) -> AuthorizationPolicyProfileRef:
        return AuthorizationPolicyProfileRef(self.policy_profile_id, self.revision)


@dataclass(frozen=True, slots=True)
class AuthorizationPolicyAssignment:
    """Exact profile revision reference assigned to one principal.

    The assignment record is configuration only. Authority still comes from the active
    ``AuthorizationProvider`` after an authorized application/translation step.
    """

    profile_ref: AuthorizationPolicyProfileRef
    principal_ref: str
    actor_types: tuple[ActorType, ...]
    assigned_by: str
    assignment_id: str = field(default_factory=lambda: new_id("authorization_policy_assignment"))
    project_id: str | None = None
    organization_id: str | None = None
    team_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.assignment_id, "authorization_policy_assignment")
        _non_blank(self.principal_ref, "principal_ref")
        _non_blank(self.assigned_by, "assigned_by")
        actor_types = tuple(self.actor_types)
        if not actor_types:
            raise ValueError("policy assignment requires at least one actor type")
        if len(actor_types) != len(set(actor_types)):
            raise ValueError("actor_types must not contain duplicates")
        object.__setattr__(self, "actor_types", actor_types)
        if self.project_id is not None:
            validate_id(self.project_id, "project")
        if self.organization_id is not None:
            validate_id(self.organization_id, "organization")
        if self.team_id is not None:
            validate_id(self.team_id, "team")
            if self.organization_id is None:
                raise ValueError("team-scoped assignments require organization_id")
        _aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class AuthorizationPolicyProfileCallContext:
    operation: OperationContext
    actor_ref: str
    organization_id: str | None = None
    team_id: str | None = None
    approval_id: str | None = None

    def __post_init__(self) -> None:
        _non_blank(self.actor_ref, "policy profile actor_ref")
        if self.organization_id is not None:
            validate_id(self.organization_id, "organization")
        if self.team_id is not None:
            validate_id(self.team_id, "team")
