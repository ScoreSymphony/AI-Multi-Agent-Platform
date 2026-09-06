"""#15 authorization boundary for canonical reusable workflows."""

from __future__ import annotations

from dataclasses import dataclass, replace

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import FrozenJsonValue, JsonValue, OperationContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.security import (
    AuthorizationAction,
    AuthorizationContext,
    AuthorizationGate,
    ProposedAction,
    ResourceType,
    RiskClassification,
    infer_actor_identity,
)

from .models import WorkflowContent, WorkflowDefinition, WorkflowRevision, WorkflowRevisionRef
from .service import WorkflowAdmission, WorkflowService


@dataclass(frozen=True, slots=True)
class WorkflowCallContext:
    operation: OperationContext
    actor_ref: str
    approval_id: str | None = None

    def __post_init__(self) -> None:
        if not self.actor_ref.strip():
            raise ValueError("workflow actor_ref must not be blank")


class AuthorizedWorkflowService:
    """Policy-enforced facade over :class:`WorkflowService`.

    Scope is always derived from the stored workflow definition for existing resources.
    Callers cannot substitute another Project/Organization merely by changing their
    ``OperationContext``.
    """

    def __init__(self, workflows: WorkflowService, authorization: AuthorizationGate) -> None:
        self.workflows = workflows
        self.authorization = authorization

    async def create(
        self,
        *,
        context: WorkflowCallContext,
        owner_ref: OwnerRef,
        content: WorkflowContent,
        project_id: str | None = None,
        organization_id: str | None = None,
        workflow_id: str | None = None,
    ) -> WorkflowRevision:
        from .models import new_workflow_id

        stable_id = workflow_id or new_workflow_id()
        await self._enforce(
            stable_id,
            AuthorizationAction.CREATE,
            context,
            project_id=project_id,
            organization_id=organization_id,
        )
        return self.workflows.create(
            owner_ref=owner_ref,
            content=content,
            project_id=project_id,
            organization_id=organization_id,
            workflow_id=stable_id,
        )

    async def revise(
        self,
        workflow_id: str,
        content: WorkflowContent,
        *,
        context: WorkflowCallContext,
        expected_revision: int,
    ) -> WorkflowRevision:
        definition = self.workflows.get(workflow_id)
        await self._enforce_definition(
            definition,
            AuthorizationAction.MODIFY,
            context,
            payload={"expected_revision": expected_revision},
        )
        return self.workflows.revise(
            workflow_id,
            content,
            expected_revision=expected_revision,
        )

    async def get(
        self,
        workflow_id: str,
        *,
        context: WorkflowCallContext,
    ) -> WorkflowDefinition:
        definition = self.workflows.get(workflow_id)
        await self._enforce_definition(definition, AuthorizationAction.READ, context)
        return definition

    async def list(
        self,
        *,
        context: WorkflowCallContext,
    ) -> tuple[WorkflowDefinition, ...]:
        visible: list[WorkflowDefinition] = []
        for definition in self.workflows.list():
            try:
                await self._enforce_definition(definition, AuthorizationAction.READ, context)
            except ContractError as exc:
                if exc.code is ErrorCode.FORBIDDEN:
                    continue
                raise
            visible.append(definition)
        return tuple(visible)

    async def resolve(
        self,
        reference: WorkflowRevisionRef,
        *,
        context: WorkflowCallContext,
    ) -> WorkflowRevision:
        definition = self.workflows.get(reference.workflow_id)
        await self._enforce_definition(
            definition,
            AuthorizationAction.READ,
            context,
            payload={"revision": reference.revision},
        )
        return self.workflows.resolve(reference)

    async def list_revisions(
        self,
        workflow_id: str,
        *,
        context: WorkflowCallContext,
    ) -> tuple[WorkflowRevision, ...]:
        definition = self.workflows.get(workflow_id)
        await self._enforce_definition(
            definition,
            AuthorizationAction.READ,
            context,
            payload={"revision_history": True},
        )
        return self.workflows.list_revisions(workflow_id)

    def compensate_created(
        self,
        workflow_id: str,
        *,
        expected_owner_ref: OwnerRef,
        expected_source: str,
        expected_instance_id: str,
    ) -> None:
        """Internal rollback-only seam; this is not a user-facing delete operation."""

        self.workflows.compensate_created(
            workflow_id,
            expected_owner_ref=expected_owner_ref,
            expected_source=expected_source,
            expected_instance_id=expected_instance_id,
        )

    async def admit(
        self,
        reference: WorkflowRevisionRef,
        *,
        context: WorkflowCallContext,
        task_id: str,
        owner_ref: OwnerRef,
        parameters: dict[str, FrozenJsonValue] | None = None,
    ) -> WorkflowAdmission:
        definition = self.workflows.get(reference.workflow_id)
        await self._enforce_definition(
            definition,
            AuthorizationAction.EXECUTE,
            context,
            payload={"revision": reference.revision, "task_id": task_id},
        )
        return self.workflows.admit(
            reference,
            task_id=task_id,
            owner_ref=owner_ref,
            parameters=parameters,
        )

    async def _enforce_definition(
        self,
        definition: WorkflowDefinition,
        action: AuthorizationAction,
        context: WorkflowCallContext,
        *,
        payload: dict[str, JsonValue] | None = None,
    ) -> None:
        await self._enforce(
            definition.workflow_id,
            action,
            context,
            project_id=definition.project_id,
            organization_id=definition.organization_id,
            payload=payload,
        )

    async def _enforce(
        self,
        workflow_id: str,
        action: AuthorizationAction,
        context: WorkflowCallContext,
        *,
        project_id: str | None,
        organization_id: str | None,
        payload: dict[str, JsonValue] | None = None,
    ) -> None:
        operation = replace(context.operation, project_id=project_id)
        actor = infer_actor_identity(context.actor_ref, organization_id=organization_id)
        proposed = ProposedAction(
            AuthorizationContext(
                actor=actor,
                action=action,
                resource_type=ResourceType.GENERIC,
                resource_id=workflow_id,
                operation=operation,
                organization_id=organization_id,
                security_labels=("workflow", action.value),
                trust_context={"canonical_domain": "workflow"},
            ),
            payload=payload,
        )
        risk = (
            RiskClassification.STANDARD
            if action is AuthorizationAction.READ
            else RiskClassification.ELEVATED
        )
        await self.authorization.enforce(
            proposed,
            approval_id=context.approval_id,
            risk=risk,
        )
