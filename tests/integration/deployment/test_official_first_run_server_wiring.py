from pathlib import Path

from ai_multi_agent_platform.adapters.single_node_app import build_default_single_node_deployment
from ai_multi_agent_platform.deployment import SingleNodeConfig
from ai_multi_agent_platform.onboarding.multi_agent_first_run import (
    ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND,
)


def test_shipped_single_node_app_advertises_official_multi_agent_first_run(
    tmp_path: Path,
) -> None:
    deployment = build_default_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
    )

    assert (
        ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND
        in deployment.control_plane.registered_commands
    )
