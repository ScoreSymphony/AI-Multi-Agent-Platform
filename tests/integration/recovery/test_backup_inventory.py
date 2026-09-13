from ai_multi_agent_platform.backup.inventory import optional_single_node_store_paths


def test_upgrade_state_participates_in_single_node_backup_inventory() -> None:
    optional = set(optional_single_node_store_paths())

    assert "db/platform-upgrade.json" in optional
    assert "db/migration-history.json" in optional
    assert "db/upgrade-history.json" in optional
    assert "db/upgrade-maintenance.json" not in optional
