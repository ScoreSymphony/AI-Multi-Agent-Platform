"""Compatibility façade for the historical hardened Automation Control Plane.

#982 moved canonical Automation ownership and Search integration to
``automation_explicit_composition``. Keep this import surface for callers that still
reference the historical module, but do not maintain a second composition path.
"""

from .automation_explicit_composition import (
    ControlPlane,
    _owned_automation_resource,
    _owned_delivery_resource,
)

__all__ = ["ControlPlane"]
