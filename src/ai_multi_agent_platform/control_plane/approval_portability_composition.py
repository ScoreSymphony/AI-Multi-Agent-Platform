"""Single-node composition joining canonical Approval decisions with portability.

This module is intentionally not exported from ``control_plane.__init__``. Importing the
portability stack while the package root is being initialized would re-enter the Agent
and Template packages and create a circular import. Deployment composition imports this
module only after the Agent package is fully initialized.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.decisions import (
    DecisionRepository,
    DecisionService,
    SqliteDecisionRepository,
    decision_record_command_handlers,
    decision_record_resource_services,
)
from ai_multi_agent_platform.governance.control_plane import register_governance_control_plane
from ai_multi_agent_platform.governance.repository import (
    GovernanceRepository,
    SqliteGovernanceRepository,
)
from ai_multi_agent_platform.governance.service import GovernanceService
from ai_multi_agent_platform.security import AuthorizationGate

from .approval_decision_composition import ControlPlane as _ApprovalControlPlane
from .extensions import _singular, _validate_resources
from .models import PageQuery, RequestContext, paginate
from .portability_api import ControlPlane as _PortabilityControlPlane


class ControlPlane(_ApprovalControlPlane, _PortabilityControlPlane):
    """Approval-aware Control Plane with portability and durable governance/decisions."""

    def __init__(
        self,
        *args: Any,
        governance_repository: GovernanceRepository | None = None,
        governance_state_path: str | Path | None = None,
        decision_repository: DecisionRepository | None = None,
        decision_state_path: str | Path | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
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
            register_governance_control_plane(self, governance)
            self.governance = governance

        decision_repo = decision_repository
        if decision_repo is None:
            state_path = decision_state_path or _default_decision_state_path(gate)
            if state_path is not None:
                decision_repo = SqliteDecisionRepository(state_path)
        if decision_repo is not None:
            decisions = DecisionService(decision_repo)
            for collection, service in decision_record_resource_services(decisions).items():
                self.register_resource_service(collection, service)
            for command, handler in decision_record_command_handlers(decisions).items():
                self.register_command(command, handler)
            self.decisions = decisions

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


__all__ = ["ControlPlane"]
