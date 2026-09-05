"""Single-node composition joining canonical Approval decisions with portability.

This module is intentionally not exported from ``control_plane.__init__``. Importing the
portability stack while the package root is being initialized would re-enter the Agent
and Template packages and create a circular import. Deployment composition imports this
module only after the Agent package is fully initialized.
"""

from __future__ import annotations

from .approval_decision_composition import ControlPlane as _ApprovalControlPlane
from .portability_api import ControlPlane as _PortabilityControlPlane


class ControlPlane(_ApprovalControlPlane, _PortabilityControlPlane):
    """Approval-aware Control Plane that also consumes the portability workflow."""


__all__ = ["ControlPlane"]
