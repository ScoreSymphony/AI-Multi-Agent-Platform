"""Authorization-gated lifecycle service for canonical policy profiles."""

from __future__ import annotations

import hashlib
from dataclasses import replace

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef, new_id, validate_id

from .authorization import (
    ActorType,
    AuthorizationAction,
    AuthorizationContext,
    ProposedAction,
    ResourceType,
    RiskClassification,
    infer_actor_identity,
)
from .enforcement import AuthorizationGate
from .policy_profile_models import (
    AuthorizationPolicyAssignment,
    AuthorizationPolicyProfileCallContext,
    AuthorizationPolicyProfileContent,
    AuthorizationPolicyProfileDefinition,
    AuthorizationPolicyProfileRef,
    AuthorizationPolicyProfileRevision,
    _non_blank,
    utc_now,
)
from .policy_profile_repository import (
    AuthorizationPolicyProfileRepository,
    InMemoryAuthorizationPolicyProfileRepository,
)


def _approval_fingerprint(*values: object) -> str:
    """Short-lived exact-action fingerprint for the authorization boundary."""

    return hashlib.sha256(repr(values).encode("utf-8")).hexdigest()


class AuthorizationPolicyProfileService:
    """Canonical lifecycle boundary for authorization-gated policy profile mutations."""

    def __init__(
        self,
        repository: AuthorizationPolicyProfileRepository,
        authorization: AuthorizationGate,
    ) -> None:
        self._repository = repository
        self._authorization = authorization

    async def _resolve_create_profile_id(
        self,
        policy_profile_id: str | None,
        context: AuthorizationPolicyProfileCallContext,
    ) -> str:
        if policy_profile_id is not None:
            return policy_profile_id
        if context.approval_id is None:
            return new_id("authorization_policy_profile")

        approval = await self._authorization.runtime_approvals.get(context.approval_id)
        expected_prefix = f"{approval.resource_id}@create:sha256:"
        if (
            approval.requester_ref != context.actor_ref
            or approval.action != AuthorizationAction.CREATE.value
            or approval.resource_type != ResourceType.GENERIC.value
            or approval.payload_ref is None
            or not approval.payload_ref.startswith(expected_prefix)
        ):
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "approval does not belong to an authorization policy profile create action",
            )
        validate_id(approval.resource_id, "authorization_policy_profile")
        return approval.resource_id

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
        profile_id = await self._resolve_create_profile_id(policy_profile_id, context)
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
        fingerprint = _approval_fingerprint(
            owner_ref,
            content,
            project_id,
            organization_id,
            team_id,
        )
        await self._enforce(
            action=AuthorizationAction.CREATE,
            resource_id=profile_id,
            context=context,
            project_id=project_id,
            organization_id=organization_id,
            team_id=team_id,
            payload_ref=f"{profile_id}@create:sha256:{fingerprint}",
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
        fingerprint = _approval_fingerprint(definition, revisions)
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

        This is an internal transaction-compensation seam for portability. It intentionally
        does not act as a general policy-profile deletion API.
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
        next_revision = expected_revision + 1
        fingerprint = _approval_fingerprint(content)
        await self._enforce_definition(
            AuthorizationAction.MODIFY,
            current,
            context,
            payload_ref=f"{policy_profile_id}@{next_revision}:sha256:{fingerprint}",
            side_effect="policy_profile_revise",
            risk=RiskClassification.HIGH,
        )
        if current.current_revision != expected_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "policy profile revision conflict",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": current.current_revision,
                },
            )
        now = utc_now()
        updated = replace(
            current,
            current_revision=next_revision,
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
            payload_ref=AuthorizationPolicyProfileRef(
                current.policy_profile_id,
                current.current_revision,
            ).token,
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
            payload_ref=AuthorizationPolicyProfileRef(
                current.policy_profile_id,
                current.current_revision,
            ).token,
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
        _non_blank(principal_ref, "principal_ref")
        normalized_actor_types = tuple(actor_types)
        if not normalized_actor_types:
            raise ValueError("policy assignment requires at least one actor type")
        if len(normalized_actor_types) != len(set(normalized_actor_types)):
            raise ValueError("actor_types must not contain duplicates")
        fingerprint = _approval_fingerprint(
            profile_ref,
            principal_ref,
            tuple(actor_type.value for actor_type in normalized_actor_types),
        )
        await self._enforce_definition(
            AuthorizationAction.ADMINISTER,
            definition,
            context,
            payload_ref=f"{profile_ref.token}@assign:sha256:{fingerprint}",
            side_effect="policy_profile_assign",
            risk=RiskClassification.CRITICAL,
        )
        revision = self._repository.get_revision(
            profile_ref.policy_profile_id,
            profile_ref.revision,
        )
        if not definition.enabled:
            raise ContractError(ErrorCode.CONFLICT, "disabled policy profile cannot be assigned")
        assignment = AuthorizationPolicyAssignment(
            profile_ref=profile_ref,
            principal_ref=principal_ref,
            actor_types=normalized_actor_types,
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
        operation = replace(context.operation, project_id=project_id)
        actor = infer_actor_identity(context.actor_ref)
        proposed = ProposedAction(
            AuthorizationContext(
                actor=actor,
                action=action,
                resource_type=ResourceType.GENERIC,
                resource_id=resource_id,
                operation=operation,
                organization_id=organization_id,
                team_id=team_id,
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
