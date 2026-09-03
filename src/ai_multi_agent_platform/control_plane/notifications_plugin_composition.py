"""Compatibility export for the runtime-complete notification Control Plane.

The composition is deliberately linear: Automation/runtime + plugins + terminal are provided by
``notifications_composition``'s base, and ``notifications_live`` adds recipient-scoped SSE on top.
"""

from .notifications_live import ControlPlane, ControlPlaneASGI, ControlPlaneHTTP, build_openapi

__all__ = ["ControlPlane", "ControlPlaneASGI", "ControlPlaneHTTP", "build_openapi"]
