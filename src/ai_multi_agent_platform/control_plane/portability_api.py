"""Canonical Control Plane surface for issue #79 portability workflows.

Portability behavior is registered through an explicit ``ControlPlaneModule``.  The
``ControlPlane`` class remains only as a compatibility construction facade for callers
that still import this module directly; it no longer owns command dispatch, collection
guards or MRO-sensitive behavior.
"""

from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.portability.workflow import PortabilityWorkflowService

from .conversation_current_composition import ControlPlane as _CurrentControlPlane
from .module_registry import install_control_plane_modules
from .portability_module import (
    PORTABILITY_COLLECTIONS,
    PORTABILITY_COMMANDS,
    PORTABILITY_MODULE,
    PORTABILITY_PACKAGE_COLLECTION,
    PORTABILITY_PREVIEW_COLLECTION,
    PORTABILITY_REPORT_COLLECTION,
    portability_control_plane_module,
)


class ControlPlane(_CurrentControlPlane):
    """Compatibility facade that installs the explicit portability module."""

    def __init__(
        self,
        *args: Any,
        portability_workflow: PortabilityWorkflowService | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._portability_workflow = portability_workflow
        if portability_workflow is not None:
            install_control_plane_modules(
                self,
                (portability_control_plane_module(portability_workflow),),
            )

    @property
    def portability_workflow(self) -> PortabilityWorkflowService | None:
        return self._portability_workflow


__all__ = [
    "PORTABILITY_COLLECTIONS",
    "PORTABILITY_COMMANDS",
    "PORTABILITY_MODULE",
    "PORTABILITY_PACKAGE_COLLECTION",
    "PORTABILITY_PREVIEW_COLLECTION",
    "PORTABILITY_REPORT_COLLECTION",
    "ControlPlane",
    "portability_control_plane_module",
]
