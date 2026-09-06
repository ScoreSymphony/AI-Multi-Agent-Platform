"""Lifecycle and task-bound admission for reusable workflow definitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import FrozenJsonValue, JsonValue
from ai_multi_agent_platform.domain import OwnerRef, Plan, Provenance, Step, new_id

from .models import (
    WorkflowContent,
    WorkflowDefinition,
    WorkflowRevision,
    WorkflowRevisionRef,
    new_workflow_id,
    utc_now,
    validate_workflow_content,
)
from .repository import WorkflowRepository


@dataclass(frozen=True, slots=True)
class WorkflowAdmission:
    """One exact reusable revision materialized into ordinary task-bound execution state."""

    source: WorkflowRevisionRef
    plan: Plan
    steps: tuple[Step, ...]


class WorkflowService:
    """Owning lifecycle service for durable reusable workflow definitions."""

    def __init__(self, repository: WorkflowRepository) -> None:
        self.repository = repository

    def create(
        self,
        *,
        owner_ref: OwnerRef,
        content: WorkflowContent,
        project_id: str | None = None,
        organization_id: str | None = None,
        workflow_id: str | None = None,
    ) -> WorkflowRevision:
        validate_workflow_content(content)
        stable_id = workflow_id or new_workflow_id()
        now = utc_now()
        definition = WorkflowDefinition(
            workflow_id=stable_id,
            owner_ref=owner_ref,
            current_revision=1,
            project_id=project_id,
            organization_id=organization_id,
            created_at=now,
            updated_at=now,
        )
        revision = WorkflowRevision(
            workflow_id=stable_id,
            revision=1,
            owner_ref=owner_ref,
            content=content,
            project_id=project_id,
            organization_id=organization_id,
            created_at=now,
        )
        self.repository.create_workflow(definition, revision)
        return revision

    def revise(
        self,
        workflow_id: str,
        content: WorkflowContent,
        *,
        expected_revision: int,
    ) -> WorkflowRevision:
        validate_workflow_content(content)
        definition = self.repository.get_workflow(workflow_id)
        if definition.current_revision != expected_revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "workflow changed since the requested revision",
                details={
                    "expected_revision": expected_revision,
                    "current_revision": definition.current_revision,
                },
            )
        next_revision = definition.current_revision + 1
        now = utc_now()
        revision = WorkflowRevision(
            workflow_id=workflow_id,
            revision=next_revision,
            owner_ref=definition.owner_ref,
            content=content,
            project_id=definition.project_id,
            organization_id=definition.organization_id,
            created_at=now,
        )
        updated = replace(
            definition,
            current_revision=next_revision,
            updated_at=now,
        )
        self.repository.append_revision(updated, revision)
        return revision

    def get(self, workflow_id: str) -> WorkflowDefinition:
        return self.repository.get_workflow(workflow_id)

    def list(self) -> tuple[WorkflowDefinition, ...]:
        return self.repository.list_workflows()

    def resolve(self, reference: WorkflowRevisionRef) -> WorkflowRevision:
        return self.repository.get_revision(reference.workflow_id, reference.revision)

    def list_revisions(self, workflow_id: str) -> tuple[WorkflowRevision, ...]:
        return self.repository.list_revisions(workflow_id)

    def compensate_created(
        self,
        workflow_id: str,
        *,
        expected_owner_ref: OwnerRef,
        expected_source: str,
        expected_instance_id: str,
    ) -> None:
        """Rollback an untouched workflow created by one failed Template apply."""

        self.repository.compensate_created(
            workflow_id,
            expected_owner_ref=expected_owner_ref,
            expected_source=expected_source,
            expected_instance_id=expected_instance_id,
        )

    def admit(
        self,
        reference: WorkflowRevisionRef,
        *,
        task_id: str,
        owner_ref: OwnerRef,
        parameters: Mapping[str, FrozenJsonValue] | None = None,
    ) -> WorkflowAdmission:
        """Materialize one exact revision into new canonical ``Plan``/``Step`` objects.

        The reusable definition is read-only during admission. Parameter values are validated
        for declared names but are intentionally not copied into Plan/Step persistence here;
        later runtime binding remains owned by the task execution layer.
        """

        revision = self.resolve(reference)
        supplied = dict(parameters or {})
        declared = {parameter.name: parameter for parameter in revision.content.parameters}
        unknown = set(supplied) - set(declared)
        if unknown:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "workflow admission contains undeclared parameters",
                details={"parameters": cast(JsonValue, sorted(unknown))},
            )
        missing = {
            name
            for name, parameter in declared.items()
            if parameter.required and name not in supplied
        }
        if missing:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "workflow admission is missing required parameters",
                details={"parameters": cast(JsonValue, sorted(missing))},
            )

        source = f"workflow:{reference.workflow_id}@{reference.revision}"
        provenance = Provenance(
            source=source,
            actor_ref=f"{owner_ref.type}:{owner_ref.id}",
            details={
                "workflow_id": reference.workflow_id,
                "workflow_revision": reference.revision,
                "parameter_names": sorted(supplied),
            },
        )
        plan = Plan(
            task_id=task_id,
            owner_ref=owner_ref,
            project_id=revision.project_id,
            provenance=provenance,
        )
        step_ids = {stage.stage_id: new_id("step") for stage in revision.content.stages}
        steps = tuple(
            Step(
                id=step_ids[stage.stage_id],
                plan_id=plan.id,
                title=stage.title,
                owner_ref=owner_ref,
                depends_on=tuple(step_ids[item] for item in stage.depends_on),
                project_id=revision.project_id,
                provenance=Provenance(
                    source=source,
                    actor_ref=f"{owner_ref.type}:{owner_ref.id}",
                    details={"workflow_stage_id": stage.stage_id},
                ),
            )
            for stage in revision.content.stages
        )
        return WorkflowAdmission(source=reference, plan=plan, steps=steps)
