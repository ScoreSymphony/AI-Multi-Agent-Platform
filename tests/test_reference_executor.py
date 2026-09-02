from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.execution import (
    Executor,
    ExecutorRegistry,
    ReferenceExecutor,
)

from .executor_contract_suite import ExecutorContractSuite


class TestReferenceExecutorContract(ExecutorContractSuite):
    def build_executor(self, tmp_path: Path) -> tuple[Executor, str]:
        workspace = tmp_path / "workspaces" / "run-1"
        workspace.mkdir(parents=True)
        return ReferenceExecutor(tmp_path / "workspaces"), "run-1"


def test_registry_selection_is_configuration_driven(tmp_path: Path) -> None:
    executor = ReferenceExecutor(tmp_path)
    registry, default = ExecutorRegistry.from_config(
        {"local": executor},
        default="local",
    )
    assert default is executor
    assert registry.select("local") is executor


def test_health_and_capability_metadata(tmp_path: Path) -> None:
    executor = ReferenceExecutor(tmp_path)
    descriptor = asyncio.run(executor.health())
    assert descriptor.executor_id == "reference"
    assert "echo" in descriptor.capabilities
    assert descriptor.metadata["arbitrary_commands"] is False
