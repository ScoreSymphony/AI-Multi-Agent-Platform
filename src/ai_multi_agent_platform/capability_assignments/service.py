"""Owning service for reusable canonical capability-assignment policy."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ai_multi_agent_platform.contracts import (
    AuthorizationOutcome,
    ContractError,
    ErrorCode,
    JsonValue,
)
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.security import (
    AuthorizationAction,
    AuthorizationContext,
    ProposedAction,
    ResourceType,
    RiskClassification,
)

from .contracts import (
    CapabilityAssignmentAccessContext,
    CapabilityAssignmentAuthorizationGate,
    CapabilityAssignmentTargetResolver,
    CapabilityInventory,
    ResolvedCapabilityAssignmentTarget,
)
from .models import (
    CapabilityAssignmentContent,
    CapabilityAssignmentPolicy,
    CapabilityAssignmentRevision,
    utc_now,
)
from .repository import CapabilityAssignmentRepository
from .validation import assignment_risk, validate_capability_rules


@dataclass(slots=True)
class CapabilityAssignmentService:
    """Validate, authorize and persist canonical assignment policy revisions."""

    repository: CapabilityAssignmentRepository
    capabilities: CapabilityInventory
    targets: CapabilityAssignmentTargetResolver
    authorization: CapabilityAssignmentAuthorizationGate

    async def create(
        self,
        *,
        owner_ref: OwnerRef,
        content: CapabilityAssignmentContent,
        access: CapabilityAssignmentAccessContext,
        project_id: str | None = None,
        organization_id: str | None = None,
        assignment_id: str | None = None,
    ) -> CapabilityAssignmentRevision:
        canonical_id = assignment_id or new_id("cap_assignment")
        now = utc_now()
        policy = CapabilityAssignmentPolicy(
            assignment_id=canonical_id,
            owner_ref=owner_ref,
            current_revision=1,
            project_id=project_id,
            organization_id=organization_id,
            created_at=now,
            updated_at=now,
        )
        revision = CapabilityAssignmentRevision(
            assignment_id=canonical_id,
            revision=1,
            owner_ref=owner_ref,
            content=content,
            project_id=project_id,
            organization_id=organization_id,
            created_at=now,
        )
        provisional_scope = ResolvedCapabilityAssignmentTarget(
            project_id=project_id,
            organization_id=organization_id,
        )
        await self.authorization.enforce(
            self._action(
                access,
                AuthorizationAction.CREATE,
                policy,
                revision,
                provisional_scope,
            ),
            approval_id=access.approval_id,
            risk=assignment_risk(content),
        )
        self._validate(content, project_id, organization_id)
        self.repository.create(policy, revision)
        return revision

    async def revise(
        self,
        assignment_id: str,
        content: CapabilityAssignmentContent,
        *,
        access: CapabilityAssignmentAccessContext,
        expected_revision: int | None = None,
    ) -> CapabilityAssignmentRevision:
        current = self.repository.get(assignment_id)
        now = utc_now()
        revision = CapabilityAssignmentRevision(
            assignment_id=current.assignment_id,
            revision=current.current_revision + 1,
            owner_ref=current.owner_ref,
            content=content,
            project_id=current.project_id,
            organization_id=current.organization_id,
            created_at=now,
        )
        updated = replace(
            current,
            current_revision=revision.revision,
            updated_at=now,
        )
        provisional_scope = ResolvedCapabilityAssignmentTarget(
            project_id=current.project_id,
            organization_id=current.organization_id,
        )
        await self.authorization.enforce(
            self._action(
                access,
                AuthorizationAction.MODIFY,
                updated,
                revision,
                provisional_scope,
            ),
            approval_id=access.approval_id,
            risk=assignment_risk(content),
        )
        if expected_revision is not None and expected_revision != current.current_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "capability assignment revision precondition failed",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": current.current_revision,
                },
            )
        self._validate(
            content,
            current.project_id,
            current.organization_id,
        )
        self.repository.append_revision(updated, revision)
        return revision

    async def get(
        self,
        assignment_id: str,
        *,
        access: CapabilityAssignmentAccessContext,
    ) -> CapabilityAssignmentPolicy:
        policy = self.repository.get(assignment_id)
        revision = self.repository.get_revision(assignment_id, policy.current_revision)
        stored_scope = ResolvedCapabilityAssignmentTarget(
            project_id=policy.project_id,
            organization_id=policy.organization_id,
        )
        await self.authorization.enforce(
            self._action(access, AuthorizationAction.READ, policy, revision, stored_scope),
            approval_id=access.approval_id,
            risk=RiskClassification.STANDARD,
        )
        return policy

    async def get_revision(
        self,
        assignment_id: str,
        revision: int,
        *,
        access: CapabilityAssignmentAccessContext,
    ) -> CapabilityAssignmentRevision:
        policy = self.repository.get(assignment_id)
        item = self.repository.get_revision(assignment_id, revision)
        stored_scope = ResolvedCapabilityAssignmentTarget(
            project_id=policy.project_id,
            organization_id=policy.organization_id,
        )
        await self.authorization.enforce(
            self._action(access, AuthorizationAction.READ, policy, item, stored_scope),
            approval_id=access.approval_id,
            risk=RiskClassification.STANDARD,
        )
        return item

    async def list(
        self,
        *,
        access: CapabilityAssignmentAccessContext,
        project_id: str | None = None,
        organization_id: str | None = None,
    ) -> tuple[CapabilityAssignmentPolicy, ...]:
        visible: list[CapabilityAssignmentPolicy] = []
        for policy in self.repository.list():
            if project_id is not None and policy.project_id != project_id:
                continue
            if organization_id is not None and policy.organization_id != organization_id:
                continue
            revision = self.repository.get_revision(policy.assignment_id, policy.current_revision)
            stored_scope = ResolvedCapabilityAssignmentTarget(
                project_id=policy.project_id,
                organization_id=policy.organization_id,
            )
            decision = await self.authorization.decide(
                self._action(
                    access,
                    AuthorizationAction.READ,
                    policy,
                    revision,
                    stored_scope,
                ),
                approval_id=access.approval_id,
                risk=RiskClassification.STANDARD,
            )
            if decision.outcome is AuthorizationOutcome.ALLOW:
                visible.append(policy)
        return tuple(visible)

    def _validate(
        self,
        content: CapabilityAssignmentContent,
        project_id: str | None,
        organization_id: str | None,
    ) -> ResolvedCapabilityAssignmentTarget:
        resolved = self.targets.resolve(content.target)
        if (
            project_id is not None
            and resolved.project_id is not None
            and project_id != resolved.project_id
        ):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "capability assignment project scope conflicts with target project",
                details={
                    "assignment_project_id": project_id,
                    "target_project_id": resolved.project_id,
                },
            )
        if (
            organization_id is not None
            and resolved.organization_id is not None
            and organization_id != resolved.organization_id
        ):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "capability assignment organization scope conflicts with target organization",
            )
        validate_capability_rules(content, self.capabilities)
        return resolved

    @staticmethod
    def _action(
        access: CapabilityAssignmentAccessContext,
        action: AuthorizationAction,
        policy: CapabilityAssignmentPolicy,
        revision: CapabilityAssignmentRevision,
        resolved_target: ResolvedCapabilityAssignmentTarget,
    ) -> ProposedAction:
        operation = access.operation
        if policy.project_id is not None and operation.project_id != policy.project_id:
            operation = replace(operation, project_id=policy.project_id)
        context = AuthorizationContext(
            actor=access.actor,
            action=action,
            resource_type=ResourceType.GENERIC,
            resource_id=policy.assignment_id,
            operation=operation,
            organization_id=policy.organization_id,
            capability_ref=(
                revision.content.all_rules[0].capability_id
                if len(revision.content.all_rules) == 1
                else None
            ),
            side_effect="capability_assignment_policy",
            trust_context={
                "canonical_resource_type": "capability_assignment",
                "assignment_revision": revision.revision,
                "target_type": revision.content.target.subject_type.value,
                "target_id": revision.content.target.subject_id,
                "target_project_id": resolved_target.project_id,
            },
        )
        payload: dict[str, JsonValue] = {
            "assignment_id": policy.assignment_id,
            "revision": revision.revision,
            "target_type": revision.content.target.subject_type.value,
            "target_id": revision.content.target.subject_id,
            "required_capability_ids": [item.capability_id for item in revision.content.required],
            "allowed_capability_ids": [item.capability_id for item in revision.content.allowed],
            "denied_capability_ids": [item.capability_id for item in revision.content.denied],
        }
        return ProposedAction(context=context, payload=payload)
