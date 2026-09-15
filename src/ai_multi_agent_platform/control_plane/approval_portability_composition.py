"""Single-node composition joining canonical Approval decisions with portability.

This module is intentionally not exported from ``control_plane.__init__``. Importing the
portability stack while the package root is being initialized would re-enter the Agent
and Template packages and create a circular import. Deployment composition imports this
module only after the Agent package is fully initialized.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_multi_agent_platform.automation import NO_TASK_REQUIRED, Automation, TriggerDelivery
from ai_multi_agent_platform.contracts.types import JsonValue, PlatformEvent
from ai_multi_agent_platform.decisions import (
    DecisionRepository,
    DecisionService,
    SqliteDecisionRepository,
    decision_record_command_handlers,
    decision_record_resource_services,
)
from ai_multi_agent_platform.goals import (
    EventSourcedGoalRepository,
    GoalService,
    KernelGoalTaskCreator,
    dispatch_goal_automation_delivery,
)
from ai_multi_agent_platform.goals.reconciliation import reconcile_goal_task_terminal_event
from ai_multi_agent_platform.governance.control_plane_module import governance_control_plane_module
from ai_multi_agent_platform.governance.repository import (
    GovernanceRepository,
    SqliteGovernanceRepository,
)
from ai_multi_agent_platform.governance.service import GovernanceService
from ai_multi_agent_platform.portability.workflow import PortabilityWorkflowService
from ai_multi_agent_platform.security import AuthorizationGate

from .approval_decision_composition import ControlPlane as _ApprovalControlPlane
from .extensions import ControlPlaneModule, _singular, _validate_resources
from .goal_contract import goal_command_handlers, goal_resource_services
from .models import PageQuery, RequestContext, paginate
from .module_registry import install_control_plane_modules
from .portability_module import portability_control_plane_module

GOAL_MODULE = "goals"
DECISION_RECORD_MODULE = "decision-records"


class ControlPlane(_ApprovalControlPlane):
    """Approval-aware Control Plane with explicitly registered product modules."""

    def __init__(
        self,
        *args: Any,
        portability_workflow: PortabilityWorkflowService | None = None,
        governance_repository: GovernanceRepository | None = None,
        governance_state_path: str | Path | None = None,
        decision_repository: DecisionRepository | None = None,
        decision_state_path: str | Path | None = None,
        **kwargs: Any,
    ) -> None:
        # Portability used to enter the canonical deployment through a second
        # ControlPlane base class. Install its explicit contribution instead, so
        # command/resource ownership no longer depends on Python MRO order.
        super().__init__(*args, **kwargs)
        self._portability_workflow = portability_workflow
        if portability_workflow is not None:
            install_control_plane_modules(
                self,
                (portability_control_plane_module(portability_workflow),),
            )

        # Goals are canonical product state, not an optional deployment extension. Reuse the
        # existing kernel EventRepository so Goal snapshots, idempotency records and Task work
        # survive the same local restart boundary as Tasks/Runs.
        self.goals = GoalService(
            EventSourcedGoalRepository(self._events),
            task_creator=KernelGoalTaskCreator(self._kernel),
        )
        install_control_plane_modules(
            self,
            (
                ControlPlaneModule(
                    name=GOAL_MODULE,
                    resource_services=goal_resource_services(self.goals),
                    command_handlers=goal_command_handlers(self.goals),
                ),
            ),
        )

        async def reconcile_goal_task_event(event: PlatformEvent) -> None:
            await reconcile_goal_task_terminal_event(self.goals, event)

        self.automation_runtime.register_event_preprocessor(reconcile_goal_task_event)

        self.governance: GovernanceService | None = None
        self.decisions: DecisionService | None = None
        gate = getattr(self, "approval_gate", None)
        if not isinstance(gate, AuthorizationGate):
            return

        governance_repo = governance_repository
        if governance_repo is None:
            state_path = governance_state_path or _default_governance_state_path(gate)
            if state_path is not None:
                governance_repo = SqliteGovernanceRepository(state_path)
        if governance_repo is not None:
            governance = GovernanceService(governance_repo, self._kernel, gate)
            install_control_plane_modules(
                self,
                (governance_control_plane_module(self, governance),),
            )
            self.governance = governance

        decision_repo = decision_repository
        if decision_repo is None:
            state_path = decision_state_path or _default_decision_state_path(gate)
            if state_path is not None:
                decision_repo = SqliteDecisionRepository(state_path)
        if decision_repo is not None:
            decisions = DecisionService(decision_repo)
            install_control_plane_modules(
                self,
                (
                    ControlPlaneModule(
                        name=DECISION_RECORD_MODULE,
                        resource_services=decision_record_resource_services(decisions),
                        command_handlers=decision_record_command_handlers(decisions),
                    ),
                ),
            )
            self.decisions = decisions

    @property
    def portability_workflow(self) -> PortabilityWorkflowService | None:
        return self._portability_workflow

    async def _create_task_from_automation(
        self,
        automation: Automation,
        delivery: TriggerDelivery,
        payload: dict[str, JsonValue],
        idempotency_key: str,
    ) -> str:
        dispatch = await dispatch_goal_automation_delivery(
            self.goals,
            automation,
            delivery,
            payload,
            idempotency_key,
        )
        if not dispatch.handled:
            return await super()._create_task_from_automation(
                automation,
                delivery,
                payload,
                idempotency_key,
            )
        if dispatch.generated_task_id is None:
            return NO_TASK_REQUIRED
        return dispatch.generated_task_id

    async def list_extension_resources(
        self,
        context: RequestContext,
        collection: str,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        service = self._registered_resource_service(collection)
        if not bool(getattr(service, "handles_search_and_filters", False)):
            return await super().list_extension_resources(context, collection, query)

        await self._authorize(context, f"{_singular(collection)}:list", collection)
        resources = list(await service.list_resources(context, query))
        _validate_resources(collection, resources)
        pagination = PageQuery(
            limit=query.limit,
            cursor=query.cursor,
            sort=query.sort,
            direction=query.direction,
            fields=query.fields,
        )
        return paginate(resources, pagination)


def _default_governance_state_path(gate: AuthorizationGate) -> Path | None:
    """Co-locate governance with durable #15 Approval state when such state exists."""

    database_path = getattr(gate.approvals, "database_path", None)
    if database_path is None:
        return None
    return Path(database_path).with_name("governance.sqlite3")


def _default_decision_state_path(gate: AuthorizationGate) -> Path | None:
    """Co-locate canonical Decision Records with durable #15 Approval state."""

    database_path = getattr(gate.approvals, "database_path", None)
    if database_path is None:
        return None
    return Path(database_path).with_name("decisions.sqlite3")


__all__ = ["ControlPlane", "DECISION_RECORD_MODULE", "GOAL_MODULE"]
