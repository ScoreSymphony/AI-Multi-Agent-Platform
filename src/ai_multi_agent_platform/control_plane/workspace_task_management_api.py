"""Compatibility import for module-owned Workspace/Task-management composition (#982).

The historical module path remains supported, but canonical behavior now lives in
``workspace_task_management_registered_composition``. Keeping this module behavior-free
prevents the old Run/Workspace + Task-management multiple-inheritance stack from
remaining a second composition path.
"""

from .workspace_task_management_registered_composition import (
    INSECURE_CONTROL_PLANE_ENV,
    TASK_MANAGEMENT_MODULE,
    ControlPlane,
    ControlPlaneHTTP,
    build_openapi,
)

__all__ = [
    "ControlPlane",
    "ControlPlaneHTTP",
    "INSECURE_CONTROL_PLANE_ENV",
    "TASK_MANAGEMENT_MODULE",
    "build_openapi",
]
