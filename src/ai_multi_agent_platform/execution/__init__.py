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
    "ExecutorRegistry",
    "ReferenceExecutor",
]
