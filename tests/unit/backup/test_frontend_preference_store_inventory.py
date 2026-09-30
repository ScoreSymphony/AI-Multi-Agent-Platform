from ai_multi_agent_platform.backup.inventory import (
    SINGLE_NODE_DURABLE_STORES,
    optional_single_node_store_paths,
    required_single_node_store_paths,
)


def test_frontend_preference_store_is_optional_and_backed_up_when_present() -> None:
    preference_store = next(
        spec for spec in SINGLE_NODE_DURABLE_STORES if spec.store_id == "frontend-preferences"
    )

    assert preference_store.path == "db/frontend-preferences.sqlite3"
    assert preference_store.kind == "sqlite"
    assert preference_store.owner == "frontend-preferences"
    assert preference_store.required is False
    assert preference_store.path in optional_single_node_store_paths()
    assert preference_store.path not in required_single_node_store_paths()
