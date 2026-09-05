"""Safe canonical import seam for authorization policy profiles.

This module deliberately sits in the security domain rather than in portability.  The
portability layer may decode a canonical profile history, but only this service may
materialize that history into the canonical store.  Imported profiles are always disabled
and untrusted, and no assignment is created as a side effect of import.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .authorization import (
    AuthorizationAction,
    AuthorizationContext,
    ProposedAction,
    ResourceType,
    RiskClassification,
    infer_actor_identity,
)
from .enforcement import AuthorizationGate
from .policy_profile_persistence import JsonAuthorizationPolicyProfileRepository
from .policy_profiles import (
    AuthorizationPolicyProfileCallContext,
    AuthorizationPolicyProfileDefinition,
    AuthorizationPolicyProfileRevision,
    AuthorizationPolicyProvenance,
    InMemoryAuthorizationPolicyProfileRepository,
)


class RollbackCapableAuthorizationPolicyProfileRepository(Protocol):
    """Repository surface required by rollback-safe portable import."""

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

    def set_enabled(self, definition: AuthorizationPolicyProfileDefinition) -> None: ...

    def get_profile(self, policy_profile_id: str) -> AuthorizationPolicyProfileDefinition: ...

    def get_revision(
        self,
        policy_profile_id: str,
        revision: int,
    ) -> AuthorizationPolicyProfileRevision: ...

    def list_revisions(
        self,
        policy_profile_id: str,
    ) -> tuple[AuthorizationPolicyProfileRevision, ...]: ...

    def list_assignments(
        self,
        *,
        principal_ref: str | None = None,
        policy_profile_id: str | None = None,
    ) -> tuple[object, ...]: ...

    def delete_imported_profile(self, policy_profile_id: str) -> None: ...


class PortableInMemoryAuthorizationPolicyProfileRepository(
    InMemoryAuthorizationPolicyProfileRepository
):
    """Reference repository variant with narrow compensation support for #79."""

    def delete_imported_profile(self, policy_profile_id: str) -> None:
        definition = self.get_profile(policy_profile_id)
        if definition.enabled:
            raise ContractError(
                ErrorCode.CONFLICT,
                "active policy profile cannot be removed by import compensation",
            )
        if self.list_assignments(policy_profile_id=policy_profile_id):
            raise ContractError(
                ErrorCode.CONFLICT,
                "assigned policy profile cannot be removed by import compensation",
            )
        _require_imported_untrusted_history(self.list_revisions(policy_profile_id))
        del self._profiles[policy_profile_id]
        for key in tuple(self._revisions):
            if key[0] == policy_profile_id:
                del self._revisions[key]


class PortableJsonAuthorizationPolicyProfileRepository(JsonAuthorizationPolicyProfileRepository):
    """Durable repository variant with the same guarded compensation contract."""

    def delete_imported_profile(self, policy_profile_id: str) -> None:
        definition = self.get_profile(policy_profile_id)
        if definition.enabled:
            raise ContractError(
                ErrorCode.CONFLICT,
                "active policy profile cannot be removed by import compensation",
            )
        if self.list_assignments(policy_profile_id=policy_profile_id):
            raise ContractError(
                ErrorCode.CONFLICT,
                "assigned policy profile cannot be removed by import compensation",
            )
        _require_imported_untrusted_history(self.list_revisions(policy_profile_id))
        del self._profiles[policy_profile_id]
        for key in tuple(self._revisions):
            if key[0] == policy_profile_id:
                del self._revisions[key]
        self._save()


class AuthorizationPolicyProfileImportService:
    """Authorize and materialize one complete imported immutable profile history.

    This service does not create assignments and never enables imported profiles.  A later
    assignment/application remains a separate operation through ``AuthorizationPolicyProfileService``
    and therefore passes the normal #15 authorization/approval boundary.
    """

    def __init__(
        self,
        repository: RollbackCapableAuthorizationPolicyProfileRepository,
        authorization: AuthorizationGate,
    ) -> None:
        self._repository = repository
        self._authorization = authorization

    async def import_history(
        self,
        definition: AuthorizationPolicyProfileDefinition,
        revisions: tuple[AuthorizationPolicyProfileRevision, ...],
        *,
        context: AuthorizationPolicyProfileCallContext,
        source_reference: str,
    ) -> AuthorizationPolicyProfileDefinition:
        _validate_history(definition, revisions)
        _require_missing(self._repository, definition.policy_profile_id)
        sanitized_revisions = tuple(
            _sanitize_imported_revision(revision, source_reference=source_reference)
            for revision in revisions
        )
        sanitized_definition = replace(definition, enabled=False)
        await self._authorize_import(sanitized_definition, context, source_reference)

        created = False
        try:
            for index, revision in enumerate(sanitized_revisions):
                interim = replace(
                    sanitized_definition,
                    current_revision=revision.revision,
                    enabled=False,
                    updated_at=max(sanitized_definition.created_at, revision.created_at),
                )
                if index == 0:
                    self._repository.create_profile(interim, revision)
                    created = True
                else:
                    self._repository.append_revision(interim, revision)
            self._repository.set_enabled(sanitized_definition)
            return sanitized_definition
        except Exception:
            if created:
                try:
                    self._repository.delete_imported_profile(definition.policy_profile_id)
                except Exception as rollback_error:
                    raise ContractError(
                        ErrorCode.BACKEND_ERROR,
                        "policy profile import failed and internal compensation also failed",
                        details={"policy_profile_id": definition.policy_profile_id},
                    ) from rollback_error
            raise

    def compensate_import(self, policy_profile_id: str) -> None:
        """Undo only a disabled, unassigned, imported/untrusted profile.

        This narrow operation exists solely for #79 package rollback after a later resource
        fails.  It cannot remove locally-created, enabled, assigned or trusted profiles.
        """

        self._repository.delete_imported_profile(policy_profile_id)

    async def _authorize_import(
        self,
        definition: AuthorizationPolicyProfileDefinition,
        context: AuthorizationPolicyProfileCallContext,
        source_reference: str,
    ) -> None:
        operation = replace(
            context.operation,
            project_id=definition.project_id or context.operation.project_id,
        )
        actor = infer_actor_identity(context.actor_ref, organization_id=context.organization_id)
        action = ProposedAction(
            AuthorizationContext(
                actor=actor,
                action=AuthorizationAction.CREATE,
                resource_type=ResourceType.GENERIC,
                resource_id=definition.policy_profile_id,
                operation=operation,
                organization_id=definition.organization_id or context.organization_id,
                team_id=definition.team_id or context.team_id,
                side_effect="policy_profile_import",
                security_labels=("authorization-policy-profile", "portable-import"),
            ),
            payload_ref=source_reference,
        )
        await self._authorization.enforce(
            action,
            approval_id=context.approval_id,
            risk=RiskClassification.CRITICAL,
        )


def _sanitize_imported_revision(
    revision: AuthorizationPolicyProfileRevision,
    *,
    source_reference: str,
) -> AuthorizationPolicyProfileRevision:
    provenance = revision.content.provenance
    imported_provenance = AuthorizationPolicyProvenance(
        created_by=provenance.created_by,
        source=f"portable-import:{provenance.source}",
        source_reference=source_reference,
        imported=True,
        trusted=False,
    )
    return replace(
        revision,
        content=replace(revision.content, provenance=imported_provenance),
    )


def _validate_history(
    definition: AuthorizationPolicyProfileDefinition,
    revisions: tuple[AuthorizationPolicyProfileRevision, ...],
) -> None:
    if not revisions:
        raise ContractError(ErrorCode.INVALID_CONFIGURATION, "policy profile import has no revisions")
    numbers = tuple(revision.revision for revision in revisions)
    if numbers != tuple(range(1, definition.current_revision + 1)):
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "policy profile import history must be contiguous from revision 1",
        )
    if revisions[-1].revision != definition.current_revision:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "policy profile definition does not point at imported latest revision",
        )
    for revision in revisions:
        if revision.policy_profile_id != definition.policy_profile_id:
            raise ContractError(ErrorCode.INVALID_CONFIGURATION, "policy profile history ID mismatch")
        if (
            revision.owner_ref != definition.owner_ref
            or revision.project_id != definition.project_id
            or revision.organization_id != definition.organization_id
            or revision.team_id != definition.team_id
        ):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "policy profile imported ownership scope is inconsistent",
            )


def _require_imported_untrusted_history(
    revisions: tuple[AuthorizationPolicyProfileRevision, ...],
) -> None:
    if not revisions or any(
        not revision.content.provenance.imported or revision.content.provenance.trusted
        for revision in revisions
    ):
        raise ContractError(
            ErrorCode.CONFLICT,
            "only imported untrusted policy profiles may be compensated",
        )


def _require_missing(
    repository: RollbackCapableAuthorizationPolicyProfileRepository,
    policy_profile_id: str,
) -> None:
    try:
        repository.get_profile(policy_profile_id)
    except ContractError as exc:
        if exc.code is ErrorCode.NOT_FOUND:
            return
        raise
    raise ContractError(
        ErrorCode.CONFLICT,
        f"authorization policy profile already exists: {policy_profile_id}",
    )


__all__ = [
    "AuthorizationPolicyProfileImportService",
    "PortableInMemoryAuthorizationPolicyProfileRepository",
    "PortableJsonAuthorizationPolicyProfileRepository",
    "RollbackCapableAuthorizationPolicyProfileRepository",
]
