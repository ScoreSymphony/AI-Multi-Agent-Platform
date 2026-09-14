"""Canonical northbound Approval decision compatibility composition for issue #214.

Approval lifecycle storage remains owned by #15. The historical ControlPlane symbol is
kept as a thin composition façade while #982 moves its northbound resource/commands to
an explicitly owned module.
"""

from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.security.enforcement import AuthorizationGate

from .approval_decision_module import (
    APPROVAL_APPROVE_COMMAND,
    APPROVAL_DECISION_COMMANDS,
    APPROVAL_DECISION_MODULE,
    APPROVAL_DENY_COMMAND,
    ApprovalDecisionBinding,
    approval_decision_control_plane_module,
)
from .conversation_current_composition import (
    AuthenticatedControlPlaneHTTP,
    ControlPlaneASGI,
    ControlPlaneHTTP,
)
from .conversation_current_composition import (
    build_openapi as _build_current_openapi,
)
from .module_registry import install_control_plane_modules
from .organization_audit_api import ControlPlane as _CurrentControlPlane


class ControlPlane(_CurrentControlPlane):
    """Compatibility façade installing the explicit #15 Approval decision module."""

    def __init__(
        self,
        *args: Any,
        approval_gate: AuthorizationGate | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, approval_gate=approval_gate, **kwargs)
        self.approval_gate = approval_gate
        self._approval_decision_binding: ApprovalDecisionBinding | None = None
        if approval_gate is not None:
            module, binding = approval_decision_control_plane_module(approval_gate)
            install_control_plane_modules(self, (module,))
            self._approval_decision_binding = binding


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
    include_conversations: bool = False,
    include_approval_decisions: bool = False,
) -> dict[str, Any]:
    """Build the current schema and optionally advertise the #214 decision commands."""

    commands = extension_commands
    if include_approval_decisions:
        commands = tuple(sorted(set((*commands, *APPROVAL_DECISION_COMMANDS))))
    return _build_current_openapi(
        extension_collections=extension_collections,
        extension_commands=commands,
        include_conversations=include_conversations,
    )


__all__ = [
    "APPROVAL_APPROVE_COMMAND",
    "APPROVAL_DECISION_COMMANDS",
    "APPROVAL_DECISION_MODULE",
    "APPROVAL_DENY_COMMAND",
    "AuthenticatedControlPlaneHTTP",
    "ControlPlane",
    "ControlPlaneASGI",
    "ControlPlaneHTTP",
    "build_openapi",
]
