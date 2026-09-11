"""Compatibility namespace for Task Project reassignment.

Canonical ownership lives under :mod:`ai_multi_agent_platform.task_management.reassignment`.
"""

from .service import (
    DefaultTaskProjectCompatibilityPolicy,
    PreparedTaskProjectMove,
    TaskProjectCompatibilityPolicy,
    TaskProjectMoveRequest,
    TaskProjectReassignmentService,
)

__all__ = [
    "DefaultTaskProjectCompatibilityPolicy",
    "PreparedTaskProjectMove",
    "TaskProjectCompatibilityPolicy",
    "TaskProjectMoveRequest",
    "TaskProjectReassignmentService",
]
