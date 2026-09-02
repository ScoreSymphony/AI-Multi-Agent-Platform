from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from ai_multi_agent_platform.contracts.types import AdapterMetadata
from ai_multi_agent_platform.execution import ExecutionErrorCategory, ExecutionRequest, ReferenceExecutor
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


def _request(*, workspace: str, action: str = "echo", arguments: dict[str, object] | None = None) -> ExecutionRequest:
    return ExecutionRequest(
        task_id="task-security",
        run_id="run-security",
        correlation_id="correlation-security",
        action=action,
        workspace=workspace,
        arguments=arguments or {},  # type: ignore[arg-type]
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


def test_redaction_recursively_removes_sensitive_values() -> None:
    payload = {
        "authorization": "Bearer super-secret",
        "nested": {
            "api-key": "abc123",
            "safe": "visible",
            "refresh_token": "refresh-me",
        },
        "items": [{"password": "hunter2"}],
    }

    redacted = redact_sensitive(payload)

    assert isinstance(redacted, dict)
    assert redacted["authorization"] == REDACTED
    nested = redacted["nested"]
    assert isinstance(nested, dict)
    assert nested["api-key"] == REDACTED
    assert nested["refresh_token"] == REDACTED
    assert nested["safe"] == "visible"
    items = redacted["items"]
    assert isinstance(items, list)
    assert isinstance(items[0], dict)
    assert items[0]["password"] == REDACTED


def test_secret_reference_serializes_without_plaintext_secret_material() -> None:
    reference = SecretReference(
        provider="local-keystore",
        secret_id="github-token",
        scope="project:demo",
    )

    serialized = redact_sensitive(reference)

    assert serialized == {
        "secret_reference": {
            "provider": "local-keystore",
            "secret_id": "github-token",
            "scope": "project:demo",
            "metadata": {},
        }
    }


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

    # The baseline takes only canonical policy/approval inputs. No provider or adapter
    # is required to preserve the secure path.
    assert baseline_decision(context, explicitly_allowed=False) is SecurityDecision.DENY
