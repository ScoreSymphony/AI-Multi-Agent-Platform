"""Platform-owned execution abstraction and deterministic reference executor."""

from .contracts import (
    CancellationToken,
    ExecutionArtifact,
    ExecutionError,
    ExecutionErrorCategory,
    ExecutionRequest,
    ExecutionResult,
    ExecutionStatus,
    Executor,
    ExecutorDescriptor,
)
from .lifecycle import ExecutorLifecycleBackend
from .reference import ReferenceExecutor
from .registry import ExecutorRegistry

__all__ = [
    "CancellationToken",
    "ExecutionArtifact",
    "ExecutionError",
    "ExecutionErrorCategory",
    "ExecutionRequest",
    "ExecutionResult",
    "ExecutionStatus",
    "Executor",
    "ExecutorDescriptor",
    "ExecutorLifecycleBackend",
    "ExecutorRegistry",
    "ReferenceExecutor",
]
