"""Lifecycle and task-bound admission for reusable workflow definitions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import FrozenJsonValue
from ai_multi_agent_platform.domain import OwnerRef, Plan, Provenance, Step, new_id

from .models import (
    WorkflowContent,
    WorkflowDefinition,
    WorkflowRevision,
    WorkflowRevisionRef,
    new_workflow_id,
    utc_now,
)
from .repository import WorkflowRepository

_FORBIDDEN_RUNTIME_KEYS = frozenset(
    {
        "runtime_state",
        "provider_id",
        "orchestrator_id",
        "backend_id",
        "provider_session_id",
        "orchestrator_session_id",
        "backend_session_id",
        "provider_tool_ref",
        "orchestrator_plan_id",
        "active_run_id",
        "agent_run_id",
        "worker_job_id",
        "provider_handle",
        "orchestrator_handle",
        "authorization",
        "client_secret",
        "cookie",
        "set_cookie",
        "credential",
        "credentials",
        "password",
        "private_key",
        "secret",
        "token",
        "api_key",
        "access_token",
        "refresh_token",
    }
)
_FORBIDDEN_SENSITIVE_SUFFIXES = (
    "_password",
    "_secret",
    "_token",
    "_api_key",
    "_private_key",
)


def _normalize_key(key: str) -> str:
    return key.strip().lower().replace("-", "_").replace(" ", "_")


def _is_forbidden_metadata_key(key: str) -> bool:
    normalized = _normalize_key(key)
    if normalized in _FORBIDDEN_RUNTIME_KEYS:
        return True
    return any(normalized.endswith(suffix) for suffix in _FORBIDDEN_SENSITIVE_SUFFIXES)


def _scan_safe_value(value: FrozenJsonValue, path: str) -> None:
    if isinstance(value, Mapping):
        for key, item in value.items():
            current_path = f"{path}.{key}" if path else key
            if _is_forbidden_metadata_key(key):
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "workflow contains runtime-private or secret-bearing metadata",
                    details={"path": current_path},
                )
            _scan_safe_value(item, current_path)
    elif isinstance(value, tuple):
        for index, item in enumerate(value):
            _scan_safe_value(item, f"{path}[{index}]")


def validate_workflow_content(content: WorkflowContent) -> None:
    """Reject provider/orchestrator-private state and plaintext secret material."""

    _scan_safe_value(content.metadata, "metadata")
    _scan_safe_value(content.compatibility.metadata, "compatibility.metadata")
    _scan_safe_value(content.provenance.metadata, "provenance.metadata")
    for stage in content.stages:
        _scan_safe_value(stage.metadata, f"stages.{stage.stage_id}.metadata")


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
                details={"parameters": sorted(unknown)},
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
                details={"parameters": sorted(missing)},
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
