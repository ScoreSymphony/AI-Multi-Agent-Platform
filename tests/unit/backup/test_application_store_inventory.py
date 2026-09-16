from ai_multi_agent_platform.backup.inventory import (
    SINGLE_NODE_DURABLE_STORES,
    optional_single_node_store_paths,
    required_single_node_store_paths,
)


def test_application_store_is_optional_and_backed_up_when_present() -> None:
    applications = next(
        spec for spec in SINGLE_NODE_DURABLE_STORES if spec.store_id == "applications"
    )

    assert applications.path == "db/applications.sqlite3"
    assert applications.kind == "sqlite"
    assert applications.owner == "applications"
    assert applications.required is False
    assert applications.path in optional_single_node_store_paths()
    assert applications.path not in required_single_node_store_paths()
