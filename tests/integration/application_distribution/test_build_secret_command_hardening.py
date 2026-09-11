from __future__ import annotations

import pytest

from ai_multi_agent_platform.application_distribution import (
    BuildSpecification,
    BuildTarget,
    PackageType,
    control_plane,
)
from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.domain import new_id


def _target() -> BuildTarget:
    return BuildTarget(
        target_id="local-test",
        os_name="test",
        architecture="test",
        package_type=PackageType.ARCHIVE,
        output_path="dist/app.bin",
    )


@pytest.mark.parametrize(
    "command",
    (
        ("tool", "--token", "plaintext-token"),
        ("tool", "--password", "plaintext-password"),
        ("tool", "--api-key=plaintext-api-key"),
        ("tool", "--client_secret", "plaintext-client-secret"),
        ("tool", "--credential=plaintext-credential"),
        ("tool", "--private-key", "plaintext-private-key"),
    ),
)
def test_build_specification_rejects_sensitive_credential_argv_values(
    command: tuple[str, ...],
) -> None:
    with pytest.raises(ValueError, match="sensitive credential option values"):
        BuildSpecification(command=command, targets=(_target(),))


def test_control_plane_rejects_sensitive_credential_argv_values() -> None:
    with pytest.raises(ContractError) as raised:
        control_plane._build_spec(  # noqa: SLF001 - exact parser-boundary regression
            {
                "command": ["tool", "--token", "plaintext-token"],
                "targets": [
                    {
                        "target_id": "local-test",
                        "os_name": "test",
                        "architecture": "test",
                        "package_type": "archive",
                        "output_path": "dist/app.bin",
                    }
                ],
            },
            default_spec_id=new_id("build_spec"),
        )

    assert "plaintext-token" not in raised.value.message


def test_non_secret_command_options_remain_supported() -> None:
    specification = BuildSpecification(
        command=(
            "tool",
            "--mode",
            "release",
            "--token-env",
            "PRIVATE_INDEX_TOKEN",
        ),
        targets=(_target(),),
    )

    assert specification.command == (
        "tool",
        "--mode",
        "release",
        "--token-env",
        "PRIVATE_INDEX_TOKEN",
    )
