"""Repository contracts and in-memory storage for authorization policy profiles."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .policy_profile_models import (
    AuthorizationPolicyAssignment,
    AuthorizationPolicyProfileDefinition,
    AuthorizationPolicyProfileRevision,
)


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
