"""Canonical organization, team, membership, invitation and ownership models for issue #87."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.domain import OwnerRef, new_id, validate_id
from ai_multi_agent_platform.security.authorization import ActorType


def utc_now() -> datetime:
    return datetime.now(UTC)


def require_aware(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must be timezone-aware")
    return value


def _non_blank(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be blank")
    return value


def _non_blank_tuple(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    if any(not value.strip() for value in values):
        raise ValueError(f"{field_name} must not contain blank values")
    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} must not contain duplicates")
    return tuple(values)


class OrganizationStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class TeamStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


class MembershipStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    LEFT = "left"


class InvitationStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    EXPIRED = "expired"
    REVOKED = "revoked"


class ShareStatus(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class Organization:
    name: str
    owner_actor_id: str
    id: str = field(default_factory=lambda: new_id("organization"))
    display_name: str | None = None
    status: OrganizationStatus = OrganizationStatus.ACTIVE
    administrator_actor_ids: tuple[str, ...] = ()
    settings: dict[str, JsonValue] = field(default_factory=dict)
    default_policy_refs: tuple[str, ...] = ()
    default_configuration_refs: tuple[str, ...] = ()
    provenance: dict[str, JsonValue] = field(default_factory=dict)
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    archived_at: datetime | None = None

    def __post_init__(self) -> None:
        validate_id(self.id, "organization")
        _non_blank(self.name, "organization name")
        _non_blank(self.owner_actor_id, "owner_actor_id")
        if self.display_name is not None:
            _non_blank(self.display_name, "display_name")
        _non_blank_tuple(self.administrator_actor_ids, "administrator_actor_ids")
        _non_blank_tuple(self.default_policy_refs, "default_policy_refs")
        _non_blank_tuple(self.default_configuration_refs, "default_configuration_refs")
        require_aware(self.created_at, "created_at")
        require_aware(self.updated_at, "updated_at")
        if self.archived_at is not None:
            require_aware(self.archived_at, "archived_at")
        if self.status is OrganizationStatus.ARCHIVED and self.archived_at is None:
            raise ValueError("archived organizations require archived_at")


@dataclass(frozen=True, slots=True)
class Team:
    organization_id: str
    name: str
    id: str = field(default_factory=lambda: new_id("team"))
    description: str = ""
    status: TeamStatus = TeamStatus.ACTIVE
    parent_team_id: str | None = None
    project_scope_refs: tuple[str, ...] = ()
    default_policy_refs: tuple[str, ...] = ()
    default_configuration_refs: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    archived_at: datetime | None = None

    def __post_init__(self) -> None:
        validate_id(self.id, "team")
        validate_id(self.organization_id, "organization")
        _non_blank(self.name, "team name")
        if self.parent_team_id is not None:
            validate_id(self.parent_team_id, "team")
            if self.parent_team_id == self.id:
                raise ValueError("team cannot be its own parent")
        _non_blank_tuple(self.project_scope_refs, "project_scope_refs")
        _non_blank_tuple(self.default_policy_refs, "default_policy_refs")
        _non_blank_tuple(self.default_configuration_refs, "default_configuration_refs")
        require_aware(self.created_at, "created_at")
        require_aware(self.updated_at, "updated_at")
        if self.archived_at is not None:
            require_aware(self.archived_at, "archived_at")
        if self.status is TeamStatus.ARCHIVED and self.archived_at is None:
            raise ValueError("archived teams require archived_at")


@dataclass(frozen=True, slots=True)
class Membership:
    actor_id: str
    actor_type: ActorType
    organization_id: str
    id: str = field(default_factory=lambda: new_id("membership"))
    team_id: str | None = None
    status: MembershipStatus = MembershipStatus.ACTIVE
    role_refs: tuple[str, ...] = ()
    policy_refs: tuple[str, ...] = ()
    created_by_actor_id: str | None = None
    invited_by_actor_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    accepted_at: datetime = field(default_factory=utc_now)
    suspended_at: datetime | None = None
    revoked_at: datetime | None = None
    expires_at: datetime | None = None

    def __post_init__(self) -> None:
        validate_id(self.id, "membership")
        _non_blank(self.actor_id, "actor_id")
        validate_id(self.organization_id, "organization")
        if self.team_id is not None:
            validate_id(self.team_id, "team")
        _non_blank_tuple(self.role_refs, "role_refs")
        _non_blank_tuple(self.policy_refs, "policy_refs")
        if self.created_by_actor_id is not None:
            _non_blank(self.created_by_actor_id, "created_by_actor_id")
        if self.invited_by_actor_id is not None:
            _non_blank(self.invited_by_actor_id, "invited_by_actor_id")
        for field_name in (
            "created_at",
            "accepted_at",
            "suspended_at",
            "revoked_at",
            "expires_at",
        ):
            value = getattr(self, field_name)
            if value is not None:
                require_aware(value, field_name)
        if (
            self.status in {MembershipStatus.REVOKED, MembershipStatus.LEFT}
            and self.revoked_at is None
        ):
            raise ValueError("revoked/left memberships require revoked_at")
        if self.status is MembershipStatus.SUSPENDED and self.suspended_at is None:
            raise ValueError("suspended memberships require suspended_at")

    @property
    def active(self) -> bool:
        return self.status is MembershipStatus.ACTIVE


@dataclass(frozen=True, slots=True)
class Invitation:
    organization_id: str
    invited_by_actor_id: str
    expires_at: datetime
    token_ref: str
    id: str = field(default_factory=lambda: new_id("invitation"))
    team_id: str | None = None
    intended_identity_ref: str | None = None
    intended_email_ref: str | None = None
    requested_role_refs: tuple[str, ...] = ()
    requested_policy_refs: tuple[str, ...] = ()
    status: InvitationStatus = InvitationStatus.PENDING
    created_at: datetime = field(default_factory=utc_now)
    accepted_at: datetime | None = None
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        validate_id(self.id, "invitation")
        validate_id(self.organization_id, "organization")
        _non_blank(self.invited_by_actor_id, "invited_by_actor_id")
        _non_blank(self.token_ref, "token_ref")
        if self.team_id is not None:
            validate_id(self.team_id, "team")
        if self.intended_identity_ref is not None:
            _non_blank(self.intended_identity_ref, "intended_identity_ref")
        if self.intended_email_ref is not None:
            _non_blank(self.intended_email_ref, "intended_email_ref")
        if self.intended_identity_ref is None and self.intended_email_ref is None:
            raise ValueError("invitation requires an intended identity or email reference")
        _non_blank_tuple(self.requested_role_refs, "requested_role_refs")
        _non_blank_tuple(self.requested_policy_refs, "requested_policy_refs")
        require_aware(self.created_at, "created_at")
        require_aware(self.expires_at, "expires_at")
        if self.expires_at <= self.created_at:
            raise ValueError("invitation expires_at must be after created_at")
        if self.accepted_at is not None:
            require_aware(self.accepted_at, "accepted_at")
        if self.revoked_at is not None:
            require_aware(self.revoked_at, "revoked_at")
        if self.status is InvitationStatus.ACCEPTED and self.accepted_at is None:
            raise ValueError("accepted invitations require accepted_at")
        if self.status is InvitationStatus.REVOKED and self.revoked_at is None:
            raise ValueError("revoked invitations require revoked_at")


@dataclass(frozen=True, slots=True)
class ResourceOwnership:
    resource_type: str
    resource_id: str
    owner_ref: OwnerRef
    id: str = field(default_factory=lambda: new_id("resource_ownership"))
    organization_id: str | None = None
    created_by_actor_id: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.id, "resource_ownership")
        _non_blank(self.resource_type, "resource_type")
        _non_blank(self.resource_id, "resource_id")
        if self.organization_id is not None:
            validate_id(self.organization_id, "organization")
        if self.created_by_actor_id is not None:
            _non_blank(self.created_by_actor_id, "created_by_actor_id")
        require_aware(self.created_at, "created_at")
        require_aware(self.updated_at, "updated_at")


@dataclass(frozen=True, slots=True)
class ResourceShare:
    ownership_id: str
    target_ref: OwnerRef
    granted_by_actor_id: str
    id: str = field(default_factory=lambda: new_id("resource_share"))
    organization_id: str | None = None
    status: ShareStatus = ShareStatus.ACTIVE
    policy_refs: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        validate_id(self.id, "resource_share")
        validate_id(self.ownership_id, "resource_ownership")
        _non_blank(self.granted_by_actor_id, "granted_by_actor_id")
        if self.organization_id is not None:
            validate_id(self.organization_id, "organization")
        _non_blank_tuple(self.policy_refs, "policy_refs")
        require_aware(self.created_at, "created_at")
        if self.revoked_at is not None:
            require_aware(self.revoked_at, "revoked_at")
        if self.status is ShareStatus.REVOKED and self.revoked_at is None:
            raise ValueError("revoked shares require revoked_at")


@dataclass(frozen=True, slots=True)
class ExternalGroupMapping:
    provider_ref: str
    external_group_id: str
    organization_id: str
    id: str = field(default_factory=lambda: new_id("external_group_mapping"))
    team_id: str | None = None
    provisioning_mode: str = "manual"
    active: bool = True
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        validate_id(self.id, "external_group_mapping")
        _non_blank(self.provider_ref, "provider_ref")
        _non_blank(self.external_group_id, "external_group_id")
        validate_id(self.organization_id, "organization")
        if self.team_id is not None:
            validate_id(self.team_id, "team")
        _non_blank(self.provisioning_mode, "provisioning_mode")
        require_aware(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class MembershipAuthorizationScope:
    """Membership-derived scope hints for #15; this is not an authorization decision."""

    actor_id: str
    organization_id: str | None
    team_ids: tuple[str, ...]
    role_refs: tuple[str, ...]
    policy_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        _non_blank(self.actor_id, "actor_id")
        if self.organization_id is not None:
            validate_id(self.organization_id, "organization")
        for team_id in self.team_ids:
            validate_id(team_id, "team")
        _non_blank_tuple(self.role_refs, "role_refs")
        _non_blank_tuple(self.policy_refs, "policy_refs")
