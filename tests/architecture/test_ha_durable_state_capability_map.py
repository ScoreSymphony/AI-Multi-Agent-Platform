from __future__ import annotations

import tomllib
from pathlib import Path

from ai_multi_agent_platform.backup.inventory import SINGLE_NODE_DURABLE_STORES

_ROOT = Path(__file__).resolve().parents[2]
_MAP_PATH = _ROOT / "docs" / "runtime" / "control_plane_ha_state_capabilities.toml"
_ALLOWED_DISPOSITIONS = {
    "shared_sql",
    "shared_provider",
    "reconstructable",
    "unsupported",
}
_ALLOWED_SUPPORT = {
    "required",
    "conditional",
    "reconstructable",
    "single_node_only",
}
_EXPECTED_NON_INVENTORY = {
    "model-routing-profiles",
    "file-content",
    "workspace-content",
    "managed-repository-content",
    "observability-export-buffer",
    "live-worker-heartbeats",
    "ha-schema-metadata",
}


def _document() -> dict[str, object]:
    return tomllib.loads(_MAP_PATH.read_text(encoding="utf-8"))


def _rows() -> list[dict[str, object]]:
    raw_rows = _document().get("state")
    assert isinstance(raw_rows, list)
    rows: list[dict[str, object]] = []
    for row in raw_rows:
        assert isinstance(row, dict)
        rows.append(row)
    return rows


def test_ha_capability_map_has_expected_schema_and_profile() -> None:
    document = _document()
    assert document["schema_version"] == 1
    assert document["profile"] == "control-plane-active-passive-postgres-v1"
    assert document["issue"] == 956
    assert document["integration_owner_issue"] == 566


def test_every_single_node_durable_store_has_an_explicit_ha_classification() -> None:
    rows = _rows()
    map_ids = {row["id"] for row in rows}
    inventory_ids = {store.store_id for store in SINGLE_NODE_DURABLE_STORES}

    assert inventory_ids <= map_ids
    assert map_ids - inventory_ids == _EXPECTED_NON_INVENTORY


def test_ha_capability_map_ids_are_unique_and_rows_are_complete() -> None:
    rows = _rows()
    ids = [row["id"] for row in rows]
    assert len(ids) == len(set(ids))

    for row in rows:
        for field in ("id", "source", "owner", "reason"):
            value = row.get(field)
            assert isinstance(value, str)
            assert value.strip()

        disposition = row.get("disposition")
        initial_support = row.get("initial_support")
        assert disposition in _ALLOWED_DISPOSITIONS
        assert initial_support in _ALLOWED_SUPPORT

        if initial_support == "required":
            assert disposition in {"shared_sql", "shared_provider"}
        if disposition == "unsupported":
            gate = row.get("gate")
            assert isinstance(gate, str)
            assert gate.strip()
        if initial_support == "single_node_only":
            assert disposition == "unsupported"


def test_model_routing_profile_inventory_gap_stays_explicit_until_fixed() -> None:
    rows = {row["id"]: row for row in _rows()}
    routing_profiles = rows["model-routing-profiles"]

    assert routing_profiles["source"] == "db/model-routing-profiles.json"
    assert routing_profiles["current_single_node_inventory"] is False
