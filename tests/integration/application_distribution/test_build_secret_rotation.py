from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.application_distribution import (
    ApplicationBuildLifecycleBackend,
    ApplicationCommandExecutor,
    ApplicationRelease,
    BuildSpecification,
    BuildTarget,
    InMemoryApplicationReleaseRepository,
    PackageType,
    ReleaseChannel,
    ReleaseVisibility,
    execution,
)
from ai_multi_agent_platform.configuration import LocalSecretProvider
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import SecretReference
from ai_multi_agent_platform.workspaces import InMemoryRunWorkspaceBindingRepository

_SECRET_CONSUMER = "service:application-build-secrets"
_SECRET_PURPOSE = "application_build"


def _target() -> BuildTarget:
    return BuildTarget(
        target_id="local-test",
        os_name="test",
        architecture="test",
        package_type=PackageType.ARCHIVE,
        output_path="dist/app.bin",
    )


def _reference(project_id: str, secret_id: str) -> SecretReference:
    return SecretReference(
        provider="local-secrets",
        secret_id=secret_id,
        scope=project_id,
    )


def _release(
    project_id: str,
    secret_environment: dict[str, SecretReference],
) -> ApplicationRelease:
    return ApplicationRelease(
        application_id="secret-rotation-app",
        display_name="Secret Rotation App",
        version="1.0.0",
        channel=ReleaseChannel.STABLE,
        visibility=ReleaseVisibility.PRIVATE,
        project_id=project_id,
        workspace_id=new_id("workspace"),
        workspace_snapshot_id=new_id("workspace_snapshot"),
        workspace_content_checksum="d" * 64,
        source_revision="source-revision-secret-rotation",
        build_specification=BuildSpecification(
            command=("tool", "build"),
            targets=(_target(),),
            secret_environment=secret_environment,
        ),
        creator_ref="user:tester",
    )


def _lifecycle(
    tmp_path: Path,
    secret_provider: LocalSecretProvider,
) -> ApplicationBuildLifecycleBackend:
    return ApplicationBuildLifecycleBackend(
        InMemoryApplicationReleaseRepository(),
        object(),  # type: ignore[arg-type]
        object(),  # type: ignore[arg-type]
        InMemoryRunWorkspaceBindingRepository(),
        ApplicationCommandExecutor(tmp_path),
        secret_provider=secret_provider,
        secret_consumer_ref=_SECRET_CONSUMER,
        secret_purpose=_SECRET_PURPOSE,
    )


def test_multiple_secret_environment_mappings_are_deterministic_and_scoped(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        alpha = _reference(project_id, "alpha-build-secret")
        beta = _reference(project_id, "beta-build-secret")
        secrets = LocalSecretProvider()
        for reference, value in ((alpha, "alpha-value"), (beta, "beta-value")):
            await secrets.create(
                reference,
                value,
                purpose="application-build-test",
                allowed_consumers=(_SECRET_CONSUMER,),
                allowed_purposes=(_SECRET_PURPOSE,),
            )
        release = _release(
            project_id,
            {
                "BETA_SECRET": beta,
                "ALPHA_SECRET": alpha,
            },
        )

        environment, secret_names = await _lifecycle(tmp_path, secrets)._build_environment(
            release,
            task_id=new_id("task"),
            run_id=new_id("run"),
            timeout_seconds=None,
        )

        assert environment == {
            "ALPHA_SECRET": "alpha-value",
            "BETA_SECRET": "beta-value",
        }
        assert secret_names == ("ALPHA_SECRET", "BETA_SECRET")
        assert tuple(release.build_specification.secret_environment) == (
            "ALPHA_SECRET",
            "BETA_SECRET",
        )
        assert "alpha-value" not in repr(release)
        assert "beta-value" not in repr(release)

    asyncio.run(scenario())


def test_new_build_resolution_uses_rotated_secret_value(tmp_path: Path) -> None:
    async def scenario() -> None:
        project_id = new_id("project")
        reference = _reference(project_id, "rotating-build-secret")
        secrets = LocalSecretProvider()
        await secrets.create(
            reference,
            "first-value",
            purpose="application-build-test",
            allowed_consumers=(_SECRET_CONSUMER,),
            allowed_purposes=(_SECRET_PURPOSE,),
        )
        release = _release(project_id, {"ROTATING_SECRET": reference})
        lifecycle = _lifecycle(tmp_path, secrets)

        first_environment, _ = await lifecycle._build_environment(
            release,
            task_id=new_id("task"),
            run_id=new_id("run"),
            timeout_seconds=None,
        )
        await secrets.rotate(reference, "second-value")
        second_environment, _ = await lifecycle._build_environment(
            release,
            task_id=new_id("task"),
            run_id=new_id("run"),
            timeout_seconds=None,
        )

        assert first_environment["ROTATING_SECRET"] == "first-value"
        assert second_environment["ROTATING_SECRET"] == "second-value"
        assert "first-value" not in repr(release)
        assert "second-value" not in repr(release)

    asyncio.run(scenario())


def test_secret_lease_bounds_application_build_timeout() -> None:
    lease_expires_at = datetime.now(UTC) + timedelta(seconds=30)

    lease_only = execution._lease_bound_timeout_seconds(  # noqa: SLF001
        None,
        lease_expires_at,
    )
    configured_shorter = execution._lease_bound_timeout_seconds(  # noqa: SLF001
        5.0,
        lease_expires_at,
    )

    assert lease_only is not None
    assert 0 < lease_only <= 30
    assert configured_shorter is not None
    assert 0 < configured_shorter <= 5

    with pytest.raises(ContractError) as raised:
        execution._lease_bound_timeout_seconds(  # noqa: SLF001
            None,
            datetime.now(UTC) - timedelta(seconds=1),
        )
    assert raised.value.code is ErrorCode.FORBIDDEN
