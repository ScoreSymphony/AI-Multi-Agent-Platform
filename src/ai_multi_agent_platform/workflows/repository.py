"""Repository boundary for canonical reusable workflow definitions."""

from __future__ import annotations

from typing import Protocol

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import OwnerRef

from .models import WorkflowDefinition, WorkflowRevision


class WorkflowRepository(Protocol):
    def create_workflow(
        self,
        definition: WorkflowDefinition,
        revision: WorkflowRevision,
    ) -> None: ...

    def append_revision(
        self,
        definition: WorkflowDefinition,
        revision: WorkflowRevision,
    ) -> None: ...

    def get_workflow(self, workflow_id: str) -> WorkflowDefinition: ...

    def list_workflows(self) -> tuple[WorkflowDefinition, ...]: ...

    def get_revision(self, workflow_id: str, revision: int) -> WorkflowRevision: ...

    def list_revisions(self, workflow_id: str) -> tuple[WorkflowRevision, ...]: ...

    def compensate_created(
        self,
        workflow_id: str,
        *,
        expected_owner_ref: OwnerRef,
        expected_source: str,
        expected_instance_id: str,
    ) -> None: ...


class InMemoryWorkflowRepository:
    """Reference repository preserving complete immutable workflow revision history."""

    def __init__(self) -> None:
        self._workflows: dict[str, WorkflowDefinition] = {}
        self._revisions: dict[tuple[str, int], WorkflowRevision] = {}

    def create_workflow(
        self,
        definition: WorkflowDefinition,
        revision: WorkflowRevision,
    ) -> None:
        if definition.workflow_id in self._workflows:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"workflow already exists: {definition.workflow_id}",
            )
        if definition.current_revision != 1 or revision.revision != 1:
            raise ContractError(ErrorCode.CONFLICT, "new workflow must start at revision 1")
        self._validate_pair(definition, revision)
        self._workflows[definition.workflow_id] = definition
        self._revisions[(revision.workflow_id, revision.revision)] = revision

    def append_revision(
        self,
        definition: WorkflowDefinition,
        revision: WorkflowRevision,
    ) -> None:
        current = self.get_workflow(definition.workflow_id)
        expected = current.current_revision + 1
        if definition.current_revision != expected or revision.revision != expected:
            raise ContractError(
                ErrorCode.CONFLICT,
                "workflow revision must increase exactly by one",
                details={
                    "current_revision": current.current_revision,
                    "new_revision": revision.revision,
                },
            )
        self._validate_pair(definition, revision)
        key = (revision.workflow_id, revision.revision)
        if key in self._revisions:
            raise ContractError(ErrorCode.CONFLICT, "workflow revision already exists")
        self._revisions[key] = revision
        self._workflows[definition.workflow_id] = definition

    def get_workflow(self, workflow_id: str) -> WorkflowDefinition:
        try:
            return self._workflows[workflow_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, f"workflow not found: {workflow_id}") from exc

    def list_workflows(self) -> tuple[WorkflowDefinition, ...]:
        return tuple(self._workflows[key] for key in sorted(self._workflows))

    def get_revision(self, workflow_id: str, revision: int) -> WorkflowRevision:
        try:
            return self._revisions[(workflow_id, revision)]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"workflow revision not found: {workflow_id}@{revision}",
            ) from exc

    def list_revisions(self, workflow_id: str) -> tuple[WorkflowRevision, ...]:
        self.get_workflow(workflow_id)
        revisions = [
            item for (current_id, _), item in self._revisions.items() if current_id == workflow_id
        ]
        return tuple(sorted(revisions, key=lambda item: item.revision))

    def compensate_created(
        self,
        workflow_id: str,
        *,
        expected_owner_ref: OwnerRef,
        expected_source: str,
        expected_instance_id: str,
    ) -> None:
        """Remove only an untouched workflow proven to originate from one failed apply."""

        definition = self.get_workflow(workflow_id)
        if definition.owner_ref != expected_owner_ref:
            raise ContractError(
                ErrorCode.FORBIDDEN,
                "workflow compensation owner does not match",
                details={"workflow_id": workflow_id},
            )
        if definition.current_revision != 1:
            raise ContractError(
                ErrorCode.CONFLICT,
                "workflow changed after creation and cannot be compensated",
                details={
                    "workflow_id": workflow_id,
                    "current_revision": definition.current_revision,
                },
            )
        revision = self.get_revision(workflow_id, 1)
        if revision.content.provenance.source != expected_source:
            raise ContractError(
                ErrorCode.CONFLICT,
                "workflow provenance does not match failed Template apply",
                details={"workflow_id": workflow_id},
            )
        if revision.content.provenance.metadata.get("template_instance_id") != expected_instance_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "workflow Template instance provenance does not match failed apply",
                details={"workflow_id": workflow_id},
            )
        del self._workflows[workflow_id]
        for key in tuple(self._revisions):
            if key[0] == workflow_id:
                del self._revisions[key]

    @staticmethod
    def _validate_pair(
        definition: WorkflowDefinition,
        revision: WorkflowRevision,
    ) -> None:
        if definition.workflow_id != revision.workflow_id:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "workflow definition/revision ID mismatch",
            )
        if definition.current_revision != revision.revision:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "workflow definition does not point at supplied revision",
            )
        if (
            definition.owner_ref != revision.owner_ref
            or definition.project_id != revision.project_id
            or definition.organization_id != revision.organization_id
        ):
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "workflow definition scope must match latest revision snapshot",
            )
