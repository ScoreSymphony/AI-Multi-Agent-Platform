"""Public platform-owned task/run/event kernel surface."""

from ai_multi_agent_platform.domain import RunStatus, TaskStatus

from .kernel import PlatformKernel
from .models import (
    RecoveryDisposition,
    RecoveryEntry,
    RecoveryReport,
    RunState,
    TaskState,
    TERMINAL_RUN_STATUSES,
)
from .repository import (
    CommandRecord,
    CommitResult,
    EventRepository,
    EventSourcedRunRepository,
    EventSourcedTaskRepository,
    InMemoryKernelRepository,
    RunRepository,
    TaskRepository,
)
from .sqlite_repository import SqliteKernelRepository
from .state import reduce_run, reduce_task

__all__ = [
    "CommandRecord",
    "CommitResult",
    "EventRepository",
    "EventSourcedRunRepository",
    "EventSourcedTaskRepository",
    "InMemoryKernelRepository",
    "PlatformKernel",
    "RecoveryDisposition",
    "RecoveryEntry",
    "RecoveryReport",
    "RunRepository",
    "RunState",
    "RunStatus",
    "SqliteKernelRepository",
    "TERMINAL_RUN_STATUSES",
    "TaskRepository",
    "TaskState",
    "TaskStatus",
    "reduce_run",
    "reduce_task",
]
