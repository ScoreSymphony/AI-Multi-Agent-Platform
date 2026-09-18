"""Compatibility façade for the historical hardened Automation Control Plane.

Canonical Automation ownership and Search integration moved to
``automation_explicit_composition``. Keep this import surface for callers that still
reference the historical module, but do not maintain a second composition path.
"""

from .automation_explicit_composition import ControlPlane

__all__ = ["ControlPlane"]
