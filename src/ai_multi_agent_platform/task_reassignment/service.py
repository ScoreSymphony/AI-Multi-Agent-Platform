"""Compatibility import surface for Task Project reassignment.

Canonical ownership moved to :mod:`ai_multi_agent_platform.task_management.reassignment` as part of
#726. Keep this module behavior-free so supported legacy imports remain stable during migration.
"""

from ai_multi_agent_platform.task_management.reassignment import (
    DefaultTaskProjectCompatibilityPolicy,
    PreparedTaskProjectMove,
    ProjectResolver,
    TaskIdProvider,
    TaskProjectCompatibilityPolicy,
    TaskProjectMoveRequest,
    TaskProjectReassignmentService,
    WorkspaceProjectResolver,
)

__all__ = [
    "DefaultTaskProjectCompatibilityPolicy",
    "PreparedTaskProjectMove",
    "TaskProjectCompatibilityPolicy",
    "TaskProjectMoveRequest",
    "TaskProjectReassignmentService",
]
