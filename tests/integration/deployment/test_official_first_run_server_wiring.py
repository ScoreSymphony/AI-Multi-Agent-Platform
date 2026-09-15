from pathlib import Path

from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.server import (
    build_single_node_deployment as server_build_single_node_deployment,
)
from ai_multi_agent_platform.onboarding.multi_agent_first_run import (
    ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND,
)


def test_platform_server_uses_product_single_node_composition() -> None:
    assert server_build_single_node_deployment is build_single_node_deployment


def test_platform_server_advertises_official_multi_agent_first_run(tmp_path: Path) -> None:
    deployment = server_build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False)
    )

    assert (
        ONBOARDING_RUN_MULTI_AGENT_GOLDEN_PATH_COMMAND
        in deployment.control_plane.registered_commands
    )
