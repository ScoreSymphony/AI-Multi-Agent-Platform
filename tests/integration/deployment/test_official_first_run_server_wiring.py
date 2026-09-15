from ai_multi_agent_platform.deployment import build_single_node_deployment
from ai_multi_agent_platform.deployment.server import (
    build_single_node_deployment as server_build_single_node_deployment,
)


def test_platform_server_uses_product_single_node_composition() -> None:
    assert server_build_single_node_deployment is build_single_node_deployment
