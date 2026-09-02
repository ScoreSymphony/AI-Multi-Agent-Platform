from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue
from ai_multi_agent_platform.execution import ExecutionErrorCategory, ExecutionRequest, ReferenceExecutor
from ai_multi_agent_platform.security import (
    PathSecurityError,
    SecurityContext,
    SecurityDecision,
    UntrustedInputError,
    baseline_decision,
    resolve_within,
    validate_untrusted_json,
)


def _request(
    *,
    workspace: str,
    action: str = "echo",
    arguments: dict[str, JsonValue] | None = None,
) -> ExecutionRequest:
    return ExecutionRequest(
        task_id="task-security",
        run_id="run-security",
        correlation_id="correlation-security",
        action=action,
        workspace=workspace,
        arguments=arguments or {},
    )


def test_resolve_within_rejects_parent_traversal_and_absolute_paths(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()

    with pytest.raises(PathSecurityError):
        resolve_within(root, "../outside")
    with pytest.raises(PathSecurityError):
        resolve_within(root, tmp_path / "outside")


def test_resolve_within_rejects_symlink_escape_when_supported(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    escape = root / "escape"
    try:
        escape.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("directory symlinks are unavailable in this environment")

    with pytest.raises(PathSecurityError):
        resolve_within(root, "escape/secret.txt")


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


def test_validate_untrusted_json_rejects_non_json_and_non_finite_values() -> None:
    with pytest.raises(UntrustedInputError):
        validate_untrusted_json(object())
    with pytest.raises(UntrustedInputError):
        validate_untrusted_json(float("nan"))


def test_validate_untrusted_json_enforces_resource_bounds() -> None:
    with pytest.raises(UntrustedInputError):
        validate_untrusted_json({"outer": {"inner": "value"}}, max_depth=1)
    with pytest.raises(UntrustedInputError):
        validate_untrusted_json([1, 2, 3], max_items=2)
    with pytest.raises(UntrustedInputError):
        validate_untrusted_json("abcd", max_string_length=3)


def test_security_decision_is_deny_by_default() -> None:
    context = SecurityContext(
        actor_id="actor:user-1",
        action="workspace.write",
        resource_type="workspace",
        resource_id="workspace-1",
    )

    assert baseline_decision(context, explicitly_allowed=False) is SecurityDecision.DENY
    assert (
        baseline_decision(
            context,
            explicitly_allowed=True,
            approval_required=True,
            approval_granted=False,
        )
        is SecurityDecision.REQUIRE_APPROVAL
    )
    assert (
        baseline_decision(
            context,
            explicitly_allowed=True,
            approval_required=True,
            approval_granted=True,
        )
        is SecurityDecision.ALLOW
    )


def test_adapter_private_metadata_cannot_grant_authority() -> None:
    context = SecurityContext(
        actor_id="actor:user-1",
        action="admin.delete",
        resource_type="project",
        resource_id="project-1",
        adapter_metadata=(
            AdapterMetadata(
                namespace="malicious-adapter",
                values={"is_admin": True, "approved": True, "role": "owner"},
            ),
        ),
    )

    assert baseline_decision(context, explicitly_allowed=False) is SecurityDecision.DENY


def test_optional_adapter_absence_does_not_change_canonical_security_decision() -> None:
    context = SecurityContext(
        actor_id="actor:user-1",
        action="tool.invoke",
        resource_type="capability",
        resource_id="capability-1",
    )

    assert baseline_decision(context, explicitly_allowed=False) is SecurityDecision.DENY
