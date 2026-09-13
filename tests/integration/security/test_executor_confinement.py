"""Migrated from root-level security baseline coverage under #722."""

# ruff: noqa: F401

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts.types import AdapterMetadata
from ai_multi_agent_platform.execution import (
    ExecutionErrorCategory,
    ExecutionRequest,
    ReferenceExecutor,
)
from ai_multi_agent_platform.security import (
    REDACTED,
    PathSecurityError,
    SecretReference,
    SecurityContext,
    SecurityDecision,
    UntrustedInputError,
    baseline_decision,
    redact_sensitive,
    resolve_within,
    validate_untrusted_json,
)


def _request(
    *, workspace: str, action: str = "echo", arguments: dict[str, object] | None = None
) -> ExecutionRequest:
    return ExecutionRequest(
        task_id="task-security",
        run_id="run-security",
        correlation_id="correlation-security",
        action=action,
        workspace=workspace,
        arguments=arguments or {},  # type: ignore[arg-type]
    )


def test_reference_executor_rejects_workspace_traversal(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    executor = ReferenceExecutor(root)

    result = asyncio.run(executor.execute(_request(workspace="../outside")))

    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.WORKSPACE_ERROR


def test_reference_executor_rejects_artifact_symlink_escape_when_supported(tmp_path: Path) -> None:
    root = tmp_path / "workspaces"
    workspace = root / "run-1"
    outside = tmp_path / "outside"
    workspace.mkdir(parents=True)
    outside.mkdir()
    escape = workspace / "escape"
    try:
        escape.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable in this environment")

    executor = ReferenceExecutor(root)
    request = _request(
        workspace="run-1",
        action="write_artifact",
        arguments={"path": "escape/pwned.txt", "content": "must stay confined"},
    )
    result = asyncio.run(executor.execute(request))

    assert result.error is not None
    assert result.error.category is ExecutionErrorCategory.INVALID_REQUEST
    assert not (outside / "pwned.txt").exists()
