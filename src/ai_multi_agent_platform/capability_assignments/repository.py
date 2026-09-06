"""Persistence boundary for canonical capability-assignment policy resources."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef

from .models import CapabilityAssignmentPolicy, CapabilityAssignmentRevision


class CapabilityAssignmentRepository(Protocol):
    def create(
        self,
        policy: CapabilityAssignmentPolicy,
        revision: CapabilityAssignmentRevision,
    ) -> None: ...

    def append_revision(
        self,
        policy: CapabilityAssignmentPolicy,
        revision: CapabilityAssignmentRevision,
    ) -> None: ...

    def get(self, assignment_id: str) -> CapabilityAssignmentPolicy: ...

    def list(self) -> tuple[CapabilityAssignmentPolicy, ...]: ...

    def get_revision(
        self,
        assignment_id: str,
        revision: int,
    ) -> CapabilityAssignmentRevision: ...

    def list_revisions(
        self,
        assignment_id: str,
    ) -> tuple[CapabilityAssignmentRevision, ...]: ...

    def compensate_created(
        self,
        assignment_id: str,
        *,
        expected_owner_ref: OwnerRef,
        expected_source: str,
    ) -> None: ...


class InMemoryCapabilityAssignmentRepository:
    """Reference repository with contiguous immutable revision history."""

    def __init__(self) -> None:
        self._policies: dict[str, CapabilityAssignmentPolicy] = {}
        self._revisions: dict[tuple[str, int], CapabilityAssignmentRevision] = {}

    def create(
        self,
        policy: CapabilityAssignmentPolicy,
        revision: CapabilityAssignmentRevision,
    ) -> None:
        if policy.assignment_id in self._policies:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"capability assignment already exists: {policy.assignment_id}",
            )
        if policy.current_revision != 1 or revision.revision != 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                "new capability assignment must start at revision 1",
            )
        self._validate_pair(policy, revision)
        self._policies[policy.assignment_id] = policy
        self._revisions[(revision.assignment_id, 1)] = revision

    def append_revision(
        self,
        policy: CapabilityAssignmentPolicy,
        revision: CapabilityAssignmentRevision,
    ) -> None:
        current = self.get(policy.assignment_id)
        expected = current.current_revision + 1
        if policy.current_revision != expected or revision.revision != expected:
            raise ContractError(
                ErrorCode.CONFLICT,
                "capability assignment revision must increase exactly by one",
                details={
                    "current_revision": current.current_revision,
                    "new_revision": revision.revision,
                },
            )
        self._validate_pair(policy, revision)
        if (
            policy.owner_ref != current.owner_ref
            or policy.project_id != current.project_id
            or policy.organization_id != current.organization_id
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "capability assignment identity scope cannot change across revisions",
            )
        previous = self.get_revision(policy.assignment_id, current.current_revision)
        if revision.content.target != previous.content.target:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "capability assignment target cannot change across revisions",
            )
        key = (revision.assignment_id, revision.revision)
        if key in self._revisions:
            raise ContractError(
                ErrorCode.CONFLICT,
                "capability assignment revision already exists",
            )
        self._revisions[key] = revision
        self._policies[policy.assignment_id] = policy

    def get(self, assignment_id: str) -> CapabilityAssignmentPolicy:
        try:
            return self._policies[assignment_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"capability assignment not found: {assignment_id}",
            ) from exc

    def list(self) -> tuple[CapabilityAssignmentPolicy, ...]:
        return tuple(self._policies[key] for key in sorted(self._policies))

    def get_revision(
        self,
        assignment_id: str,
        revision: int,
    ) -> CapabilityAssignmentRevision:
        try:
            return self._revisions[(assignment_id, revision)]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"capability assignment revision not found: {assignment_id}@{revision}",
            ) from exc

    def list_revisions(
        self,
        assignment_id: str,
    ) -> tuple[CapabilityAssignmentRevision, ...]:
        self.get(assignment_id)
        revisions = [
            item for (current_id, _), item in self._revisions.items() if current_id == assignment_id
        ]
        return tuple(sorted(revisions, key=lambda item: item.revision))

    def compensate_created(
        self,
        assignment_id: str,
        *,
        expected_owner_ref: OwnerRef,
        expected_source: str,
    ) -> None:
        """Remove only an untouched resource proven to originate from one failed apply."""

        policy = self.get(assignment_id)
        if policy.owner_ref != expected_owner_ref:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "capability assignment compensation owner does not match",
                details={"assignment_id": assignment_id},
            )
        if policy.current_revision != 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                "capability assignment changed after creation and cannot be compensated",
                details={
                    "assignment_id": assignment_id,
                    "current_revision": policy.current_revision,
                },
            )
        revision = self.get_revision(assignment_id, 1)
        if revision.content.provenance.source != expected_source:
            raise ContractError(
                ErrorCode.CONFLICT,
                "capability assignment provenance does not match failed Template apply",
                details={"assignment_id": assignment_id},
            )
        del self._policies[assignment_id]
        for key in tuple(self._revisions):
            if key[0] == assignment_id:
                del self._revisions[key]

    @staticmethod
    def _validate_pair(
        policy: CapabilityAssignmentPolicy,
        revision: CapabilityAssignmentRevision,
    ) -> None:
        if policy.assignment_id != revision.assignment_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "capability assignment policy/revision ID mismatch",
            )
        if policy.current_revision != revision.revision:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "capability assignment policy does not point at supplied revision",
            )
        if (
            policy.owner_ref != revision.owner_ref
            or policy.project_id != revision.project_id
            or policy.organization_id != revision.organization_id
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "capability assignment scope must match latest revision snapshot",
            )
