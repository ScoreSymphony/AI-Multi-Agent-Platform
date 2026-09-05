"""Canonical durable authorization policy profiles for issue #310.

Policy profiles are provider-neutral reusable permission configuration. They never replace
``AuthorizationProvider`` or ``AuthorizationGate`` and do not grant authority merely by
existing, being imported, or being referenced. Exact revisions are immutable so historical
references remain interpretable after later edits or provider replacement.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.domain import OwnerRef, new_id, validate_id

from .authorization import (
    ActorType,
    AuthorizationAction,
    AuthorizationContext,
    LocalPrincipalPolicy,
    ProposedAction,
    ResourceType,
    RiskClassification,
    infer_actor_identity,
)
from .enforcement import AuthorizationGate

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


class AuthorizationPolicyProfileRepository(Protocol):
    def create_profile(
        self,
        definition: AuthorizationPolicyProfileDefinition,
        revision: AuthorizationPolicyProfileRevision,
    ) -> None: ...

    def append_revision(
        self,
        definition: AuthorizationPolicyProfileDefinition,
        revision: AuthorizationPolicyProfileRevision,
    ) -> None: ...

    def import_profile(
        self,
        definition: AuthorizationPolicyProfileDefinition,
        revisions: tuple[AuthorizationPolicyProfileRevision, ...],
    ) -> None: ...

    def delete_profile(self, policy_profile_id: str) -> None: ...

    def set_enabled(self, definition: AuthorizationPolicyProfileDefinition) -> None: ...

    def get_profile(self, policy_profile_id: str) -> AuthorizationPolicyProfileDefinition: ...

    def list_profiles(self) -> tuple[AuthorizationPolicyProfileDefinition, ...]: ...

    def get_revision(
        self,
        policy_profile_id: str,
        revision: int,
    ) -> AuthorizationPolicyProfileRevision: ...

    def list_revisions(
        self,
        policy_profile_id: str,
    ) -> tuple[AuthorizationPolicyProfileRevision, ...]: ...

    def create_assignment(self, assignment: AuthorizationPolicyAssignment) -> None: ...

    def get_assignment(self, assignment_id: str) -> AuthorizationPolicyAssignment: ...

    def list_assignments(
        self,
        *,
        principal_ref: str | None = None,
        policy_profile_id: str | None = None,
    ) -> tuple[AuthorizationPolicyAssignment, ...]: ...


class InMemoryAuthorizationPolicyProfileRepository:
    """Reference repository preserving immutable policy-profile revision history."""

    def __init__(self) -> None:
        self._profiles: dict[str, AuthorizationPolicyProfileDefinition] = {}
        self._revisions: dict[tuple[str, int], AuthorizationPolicyProfileRevision] = {}
        self._assignments: dict[str, AuthorizationPolicyAssignment] = {}

    def create_profile(
        self,
        definition: AuthorizationPolicyProfileDefinition,
        revision: AuthorizationPolicyProfileRevision,
    ) -> None:
        if definition.policy_profile_id in self._profiles:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"authorization policy profile already exists: {definition.policy_profile_id}",
            )
        if definition.current_revision != 1 or revision.revision != 1:
            raise ContractError(ErrorCode.CONFLICT, "new policy profile must start at revision 1")
        self._validate_pair(definition, revision)
        self._profiles[definition.policy_profile_id] = definition
        self._revisions[(revision.policy_profile_id, 1)] = revision

    def append_revision(
        self,
        definition: AuthorizationPolicyProfileDefinition,
        revision: AuthorizationPolicyProfileRevision,
    ) -> None:
        current = self.get_profile(definition.policy_profile_id)
        expected = current.current_revision + 1
        if definition.current_revision != expected or revision.revision != expected:
            raise ContractError(
                ErrorCode.CONFLICT,
                "policy profile revision must increase exactly by one",
                details={
                    "current_revision": current.current_revision,
                    "new_revision": revision.revision,
                },
            )
        self._validate_pair(definition, revision)
        key = (revision.policy_profile_id, revision.revision)
        if key in self._revisions:
            raise ContractError(ErrorCode.CONFLICT, "policy profile revision already exists")
        self._revisions[key] = revision
        self._profiles[definition.policy_profile_id] = definition

    def import_profile(
        self,
        definition: AuthorizationPolicyProfileDefinition,
        revisions: tuple[AuthorizationPolicyProfileRevision, ...],
    ) -> None:
        """Atomically insert a validated complete immutable history."""

        profile_id = definition.policy_profile_id
        if profile_id in self._profiles:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"authorization policy profile already exists: {profile_id}",
            )
        self._validate_history(definition, revisions)
        keys = tuple((profile_id, item.revision) for item in revisions)
        if any(key in self._revisions for key in keys):
            raise ContractError(ErrorCode.CONFLICT, "policy profile revision already exists")

        self._profiles[profile_id] = definition
        for revision in revisions:
            self._revisions[(profile_id, revision.revision)] = revision

    def delete_profile(self, policy_profile_id: str) -> None:
        self.get_profile(policy_profile_id)
        if any(
            item.profile_ref.policy_profile_id == policy_profile_id
            for item in self._assignments.values()
        ):
            raise ContractError(
                ErrorCode.CONFLICT,
                "policy profile with assignments cannot be deleted",
            )
        self._profiles.pop(policy_profile_id)
        for key in tuple(self._revisions):
            if key[0] == policy_profile_id:
                self._revisions.pop(key)

    def set_enabled(self, definition: AuthorizationPolicyProfileDefinition) -> None:
        current = self.get_profile(definition.policy_profile_id)
        if definition.current_revision != current.current_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "policy profile lifecycle update must target current revision",
            )
        if (
            definition.owner_ref != current.owner_ref
            or definition.project_id != current.project_id
            or definition.organization_id != current.organization_id
            or definition.team_id != current.team_id
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "policy profile lifecycle update cannot change ownership scope",
            )
        self._profiles[definition.policy_profile_id] = definition

    def get_profile(self, policy_profile_id: str) -> AuthorizationPolicyProfileDefinition:
        try:
            return self._profiles[policy_profile_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"authorization policy profile not found: {policy_profile_id}",
            ) from exc

    def list_profiles(self) -> tuple[AuthorizationPolicyProfileDefinition, ...]:
        return tuple(self._profiles[key] for key in sorted(self._profiles))

    def get_revision(
        self,
        policy_profile_id: str,
        revision: int,
    ) -> AuthorizationPolicyProfileRevision:
        try:
            return self._revisions[(policy_profile_id, revision)]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"authorization policy profile revision not found: {policy_profile_id}@{revision}",
            ) from exc

    def list_revisions(
        self,
        policy_profile_id: str,
    ) -> tuple[AuthorizationPolicyProfileRevision, ...]:
        self.get_profile(policy_profile_id)
        values = [
            item
            for (current_id, _), item in self._revisions.items()
            if current_id == policy_profile_id
        ]
        return tuple(sorted(values, key=lambda item: item.revision))

    def create_assignment(self, assignment: AuthorizationPolicyAssignment) -> None:
        if assignment.assignment_id in self._assignments:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"authorization policy assignment already exists: {assignment.assignment_id}",
            )
        definition = self.get_profile(assignment.profile_ref.policy_profile_id)
        if not definition.enabled:
            raise ContractError(ErrorCode.CONFLICT, "disabled policy profile cannot be assigned")
        self.get_revision(assignment.profile_ref.policy_profile_id, assignment.profile_ref.revision)
        key = (assignment.principal_ref, assignment.profile_ref)
        if any(
            (item.principal_ref, item.profile_ref) == key for item in self._assignments.values()
        ):
            raise ContractError(ErrorCode.CONFLICT, "policy profile revision already assigned")
        self._assignments[assignment.assignment_id] = assignment

    def get_assignment(self, assignment_id: str) -> AuthorizationPolicyAssignment:
        try:
            return self._assignments[assignment_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"authorization policy assignment not found: {assignment_id}",
            ) from exc

    def list_assignments(
        self,
        *,
        principal_ref: str | None = None,
        policy_profile_id: str | None = None,
    ) -> tuple[AuthorizationPolicyAssignment, ...]:
        values = tuple(self._assignments.values())
        if principal_ref is not None:
            values = tuple(item for item in values if item.principal_ref == principal_ref)
        if policy_profile_id is not None:
            values = tuple(
                item for item in values if item.profile_ref.policy_profile_id == policy_profile_id
            )
        return tuple(sorted(values, key=lambda item: (item.created_at, item.assignment_id)))

    @classmethod
    def _validate_history(
        cls,
        definition: AuthorizationPolicyProfileDefinition,
        revisions: tuple[AuthorizationPolicyProfileRevision, ...],
    ) -> None:
        if not revisions:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "policy profile import requires revision history",
            )
        expected = tuple(range(1, definition.current_revision + 1))
        if tuple(item.revision for item in revisions) != expected:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "policy profile import revision history must be contiguous",
            )
        for revision in revisions:
            if revision.policy_profile_id != definition.policy_profile_id:
                raise ContractError(ErrorCode.CONTRACT_VIOLATION, "policy profile ID mismatch")
            if (
                revision.owner_ref != definition.owner_ref
                or revision.project_id != definition.project_id
                or revision.organization_id != definition.organization_id
                or revision.team_id != definition.team_id
            ):
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "policy profile imported revision ownership scope is inconsistent",
                )
        cls._validate_pair(definition, revisions[-1])

    @staticmethod
    def _validate_pair(
        definition: AuthorizationPolicyProfileDefinition,
        revision: AuthorizationPolicyProfileRevision,
    ) -> None:
        if definition.policy_profile_id != revision.policy_profile_id:
            raise ContractError(ErrorCode.CONTRACT_VIOLATION, "policy profile ID mismatch")
        if definition.current_revision != revision.revision:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "policy profile definition does not point at supplied revision",
            )
        if (
            definition.owner_ref != revision.owner_ref
            or definition.project_id != revision.project_id
            or definition.organization_id != revision.organization_id
            or definition.team_id != revision.team_id
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "policy profile ownership scope must match latest revision snapshot",
            )


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


class AuthorizationPolicyProfileService:
    """Canonical lifecycle boundary; every user-visible mutation passes issue #15."""

    def __init__(
        self,
        repository: AuthorizationPolicyProfileRepository,
        authorization: AuthorizationGate,
    ) -> None:
        self._repository = repository
        self._authorization = authorization

    async def create(
        self,
        *,
        owner_ref: OwnerRef,
        content: AuthorizationPolicyProfileContent,
        context: AuthorizationPolicyProfileCallContext,
        project_id: str | None = None,
        organization_id: str | None = None,
        team_id: str | None = None,
        policy_profile_id: str | None = None,
    ) -> AuthorizationPolicyProfileDefinition:
        profile_id = policy_profile_id or new_id("authorization_policy_profile")
        definition = AuthorizationPolicyProfileDefinition(
            policy_profile_id=profile_id,
            owner_ref=owner_ref,
            current_revision=1,
            project_id=project_id,
            organization_id=organization_id,
            team_id=team_id,
        )
        revision = AuthorizationPolicyProfileRevision(
            policy_profile_id=profile_id,
            revision=1,
            owner_ref=owner_ref,
            content=content,
            project_id=project_id,
            organization_id=organization_id,
            team_id=team_id,
            created_at=definition.created_at,
        )
        await self._enforce(
            action=AuthorizationAction.CREATE,
            resource_id=profile_id,
            context=context,
            project_id=project_id,
            organization_id=organization_id,
            team_id=team_id,
            payload_ref=revision.ref.token,
            side_effect="policy_profile_create",
            risk=RiskClassification.HIGH,
        )
        self._repository.create_profile(definition, revision)
        return definition

    async def import_profile(
        self,
        *,
        definition: AuthorizationPolicyProfileDefinition,
        revisions: tuple[AuthorizationPolicyProfileRevision, ...],
        context: AuthorizationPolicyProfileCallContext,
    ) -> AuthorizationPolicyProfileDefinition:
        """Authorize and atomically persist dormant, untrusted imported configuration."""

        self._validate_import_candidate(definition, revisions)
        fingerprint = hashlib.sha256(repr((definition, revisions)).encode("utf-8")).hexdigest()
        await self._enforce(
            action=AuthorizationAction.CREATE,
            resource_id=definition.policy_profile_id,
            context=context,
            project_id=definition.project_id,
            organization_id=definition.organization_id,
            team_id=definition.team_id,
            payload_ref=(
                f"{definition.policy_profile_id}@import:{definition.current_revision}:"
                f"sha256:{fingerprint}"
            ),
            side_effect="policy_profile_import",
            risk=RiskClassification.CRITICAL,
        )
        self._repository.import_profile(definition, revisions)
        return definition

    def compensate_import(self, policy_profile_id: str) -> None:
        """Rollback only a dormant, unassigned, untrusted imported profile.

        This is an internal transaction-compensation seam for #79. It intentionally does
        not act as a general policy-profile deletion API.
        """

        definition = self._repository.get_profile(policy_profile_id)
        revisions = self._repository.list_revisions(policy_profile_id)
        if definition.enabled:
            raise ContractError(
                ErrorCode.CONFLICT,
                "enabled policy profile cannot be import-compensated",
            )
        if self._repository.list_assignments(policy_profile_id=policy_profile_id):
            raise ContractError(
                ErrorCode.CONFLICT,
                "assigned policy profile cannot be import-compensated",
            )
        if not revisions or any(
            not item.content.provenance.imported or item.content.provenance.trusted
            for item in revisions
        ):
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "only untrusted imported policy profiles may be import-compensated",
            )
        self._repository.delete_profile(policy_profile_id)

    async def get(
        self,
        policy_profile_id: str,
        context: AuthorizationPolicyProfileCallContext,
    ) -> AuthorizationPolicyProfileDefinition:
        definition = self._repository.get_profile(policy_profile_id)
        await self._enforce_definition(AuthorizationAction.READ, definition, context)
        return definition

    async def list(
        self,
        context: AuthorizationPolicyProfileCallContext,
    ) -> tuple[AuthorizationPolicyProfileDefinition, ...]:
        visible: list[AuthorizationPolicyProfileDefinition] = []
        for definition in self._repository.list_profiles():
            try:
                await self._enforce_definition(AuthorizationAction.READ, definition, context)
            except ContractError as exc:
                if exc.code is ErrorCode.FORBIDDEN:
                    continue
                raise
            visible.append(definition)
        return tuple(visible)

    async def get_revision(
        self,
        profile_ref: AuthorizationPolicyProfileRef,
        context: AuthorizationPolicyProfileCallContext,
    ) -> AuthorizationPolicyProfileRevision:
        definition = self._repository.get_profile(profile_ref.policy_profile_id)
        await self._enforce_definition(AuthorizationAction.READ, definition, context)
        return self._repository.get_revision(profile_ref.policy_profile_id, profile_ref.revision)

    async def revise(
        self,
        policy_profile_id: str,
        content: AuthorizationPolicyProfileContent,
        context: AuthorizationPolicyProfileCallContext,
        *,
        expected_revision: int,
    ) -> AuthorizationPolicyProfileDefinition:
        current = self._repository.get_profile(policy_profile_id)
        if current.current_revision != expected_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "policy profile revision conflict",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": current.current_revision,
                },
            )
        await self._enforce_definition(
            AuthorizationAction.MODIFY,
            current,
            context,
            payload_ref=f"{policy_profile_id}@{expected_revision + 1}",
            side_effect="policy_profile_revise",
            risk=RiskClassification.HIGH,
        )
        now = utc_now()
        updated = replace(
            current,
            current_revision=expected_revision + 1,
            updated_at=now,
        )
        revision = AuthorizationPolicyProfileRevision(
            policy_profile_id=policy_profile_id,
            revision=updated.current_revision,
            owner_ref=current.owner_ref,
            content=content,
            project_id=current.project_id,
            organization_id=current.organization_id,
            team_id=current.team_id,
            created_at=now,
        )
        self._repository.append_revision(updated, revision)
        return updated

    async def disable(
        self,
        policy_profile_id: str,
        context: AuthorizationPolicyProfileCallContext,
    ) -> AuthorizationPolicyProfileDefinition:
        current = self._repository.get_profile(policy_profile_id)
        await self._enforce_definition(
            AuthorizationAction.ADMINISTER,
            current,
            context,
            side_effect="policy_profile_disable",
            risk=RiskClassification.HIGH,
        )
        if not current.enabled:
            return current
        updated = replace(current, enabled=False, updated_at=utc_now())
        self._repository.set_enabled(updated)
        return updated

    async def enable(
        self,
        policy_profile_id: str,
        context: AuthorizationPolicyProfileCallContext,
    ) -> AuthorizationPolicyProfileDefinition:
        """Explicitly activate dormant configuration through the normal admin gate."""

        current = self._repository.get_profile(policy_profile_id)
        await self._enforce_definition(
            AuthorizationAction.ADMINISTER,
            current,
            context,
            side_effect="policy_profile_enable",
            risk=RiskClassification.CRITICAL,
        )
        if current.enabled:
            return current
        updated = replace(current, enabled=True, updated_at=utc_now())
        self._repository.set_enabled(updated)
        return updated

    async def assign(
        self,
        *,
        profile_ref: AuthorizationPolicyProfileRef,
        principal_ref: str,
        actor_types: tuple[ActorType, ...],
        context: AuthorizationPolicyProfileCallContext,
    ) -> AuthorizationPolicyAssignment:
        definition = self._repository.get_profile(profile_ref.policy_profile_id)
        revision = self._repository.get_revision(
            profile_ref.policy_profile_id,
            profile_ref.revision,
        )
        if not definition.enabled:
            raise ContractError(ErrorCode.CONFLICT, "disabled policy profile cannot be assigned")
        await self._enforce_definition(
            AuthorizationAction.ADMINISTER,
            definition,
            context,
            payload_ref=profile_ref.token,
            side_effect="policy_profile_assign",
            risk=RiskClassification.CRITICAL,
        )
        assignment = AuthorizationPolicyAssignment(
            profile_ref=profile_ref,
            principal_ref=principal_ref,
            actor_types=actor_types,
            assigned_by=context.actor_ref,
            project_id=revision.project_id,
            organization_id=revision.organization_id,
            team_id=revision.team_id,
        )
        self._repository.create_assignment(assignment)
        return assignment

    @staticmethod
    def _validate_import_candidate(
        definition: AuthorizationPolicyProfileDefinition,
        revisions: tuple[AuthorizationPolicyProfileRevision, ...],
    ) -> None:
        if definition.enabled:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "imported policy profile must be dormant until explicitly enabled",
            )
        InMemoryAuthorizationPolicyProfileRepository._validate_history(definition, revisions)
        if any(
            not item.content.provenance.imported or item.content.provenance.trusted
            for item in revisions
        ):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "imported policy profile revisions must be marked imported and untrusted",
            )

    async def _enforce_definition(
        self,
        action: AuthorizationAction,
        definition: AuthorizationPolicyProfileDefinition,
        context: AuthorizationPolicyProfileCallContext,
        *,
        payload_ref: str | None = None,
        side_effect: str | None = None,
        risk: RiskClassification = RiskClassification.STANDARD,
    ) -> None:
        await self._enforce(
            action=action,
            resource_id=definition.policy_profile_id,
            context=context,
            project_id=definition.project_id,
            organization_id=definition.organization_id,
            team_id=definition.team_id,
            payload_ref=payload_ref,
            side_effect=side_effect,
            risk=risk,
        )

    async def _enforce(
        self,
        *,
        action: AuthorizationAction,
        resource_id: str,
        context: AuthorizationPolicyProfileCallContext,
        project_id: str | None,
        organization_id: str | None,
        team_id: str | None,
        payload_ref: str | None,
        side_effect: str | None,
        risk: RiskClassification,
    ) -> None:
        operation = replace(
            context.operation,
            project_id=project_id or context.operation.project_id,
        )
        actor = infer_actor_identity(context.actor_ref, organization_id=context.organization_id)
        proposed = ProposedAction(
            AuthorizationContext(
                actor=actor,
                action=action,
                resource_type=ResourceType.GENERIC,
                resource_id=resource_id,
                operation=operation,
                organization_id=organization_id or context.organization_id,
                team_id=team_id or context.team_id,
                side_effect=side_effect,
                security_labels=("authorization-policy-profile",),
            ),
            payload_ref=payload_ref,
        )
        await self._authorization.enforce(
            proposed,
            approval_id=context.approval_id,
            risk=risk,
        )


def compile_local_principal_policy(
    revision: AuthorizationPolicyProfileRevision,
    *,
    principal_ref: str,
    actor_types: tuple[ActorType, ...],
) -> LocalPrincipalPolicy:
    """Compile one canonical revision into the local reference provider's private shape.

    Canonical identity remains ``revision.ref``; replacing the local provider does not
    rewrite, re-key or otherwise mutate the profile resource.
    """

    conditions = revision.content.conditions
    if (
        conditions.required_security_labels
        or conditions.allowed_node_ids
        or conditions.allowed_side_effects
    ):
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "LocalAuthorizationProvider cannot represent canonical policy conditions",
            details={"policy_profile_ref": revision.ref.token},
        )
    scope = revision.content.scope_constraints
    if scope.resource_ids:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "LocalAuthorizationProvider cannot represent resource-ID policy constraints",
            details={"policy_profile_ref": revision.ref.token},
        )
    return LocalPrincipalPolicy(
        principal_ref=principal_ref,
        actor_types=frozenset(actor_types),
        allowed_actions=frozenset(revision.content.allowed_actions),
        approval_actions=frozenset(revision.content.approval_required_actions),
        resource_types=frozenset(revision.content.resource_types),
        project_ids=frozenset(scope.project_ids),
        organization_ids=frozenset(scope.organization_ids),
        team_ids=frozenset(scope.team_ids),
        workspace_ids=frozenset(scope.workspace_ids),
    )


__all__ = [
    "POLICY_PROFILE_SCHEMA_VERSION",
    "AuthorizationPolicyAssignment",
    "AuthorizationPolicyConditions",
    "AuthorizationPolicyProfileCallContext",
    "AuthorizationPolicyProfileContent",
    "AuthorizationPolicyProfileDefinition",
    "AuthorizationPolicyProfileRef",
    "AuthorizationPolicyProfileRepository",
    "AuthorizationPolicyProfileRevision",
    "AuthorizationPolicyProfileService",
    "AuthorizationPolicyProvenance",
    "AuthorizationPolicyScopeConstraints",
    "InMemoryAuthorizationPolicyProfileRepository",
    "compile_local_principal_policy",
]
