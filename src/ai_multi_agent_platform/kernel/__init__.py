"""Public platform-owned task/run/event kernel surface."""

from ai_multi_agent_platform.domain import RunStatus, TaskStatus

from .models import (
    TERMINAL_RUN_STATUSES,
    RecoveryDisposition,
    RecoveryEntry,
    RecoveryReport,
    RunState,
    TaskState,
)
from .output_observer import OutputAttachmentObserver, OutputObservingPlatformKernel
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
from .task_mutations import TaskMutationBoundary

# Keep the long-standing public `PlatformKernel` name while adding the provider-neutral
# post-commit output observer seam.
PlatformKernel = OutputObservingPlatformKernel

__all__ = [
    "CommandRecord",
    "CommitResult",
    "EventRepository",
    "EventSourcedRunRepository",
    "EventSourcedTaskRepository",
    "InMemoryKernelRepository",
    "OutputAttachmentObserver",
    "OutputObservingPlatformKernel",
    "PlatformKernel",
    "RecoveryDisposition",
    "RecoveryEntry",
    "RecoveryReport",
    "RunRepository",
    "RunState",
    "RunStatus",
    "SqliteKernelRepository",
    "TERMINAL_RUN_STATUSES",
    "TaskMutationBoundary",
    "TaskRepository",
    "TaskState",
    "TaskStatus",
    "reduce_run",
    "reduce_task",
]
