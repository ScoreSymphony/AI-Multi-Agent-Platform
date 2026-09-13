from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MATRIX_PATH = ROOT / "docs" / "ADAPTER_SUPPORT_MATRIX.toml"
POLICY_PATH = ROOT / "docs" / "ADAPTER_SUPPORT_MATRIX.md"

REQUIRED_BOUNDARIES = {
    "planner",
    "orchestrator",
    "executor",
    "model-provider",
    "model-router",
    "capability-provider",
    "mcp-client",
    "browser-provider",
    "file-provider",
    "memory-provider",
    "knowledge-provider",
    "persistence-repository",
    "message-transport",
    "connector-provider",
    "repository-provider",
    "observability-exporter",
    "verification-provider",
    "evaluation-provider",
    "security-evidence-provider",
}
REQUIRED_FIELDS = {
    "id",
    "boundary",
    "kind",
    "symbol",
    "source",
    "tier",
    "owner",
    "local_first",
    "mandatory_paid_service",
    "unique_value",
    "overlap",
    "test_burden",
    "doc_burden",
    "security_surface",
    "upstream_health",
    "compatibility",
    "usage",
    "evidence",
    "docs",
    "migration",
}


def _matrix() -> dict[str, object]:
    return tomllib.loads(MATRIX_PATH.read_text(encoding="utf-8"))


def _implementations() -> list[dict[str, object]]:
    raw = _matrix().get("implementation")
    assert isinstance(raw, list)
    return raw


def test_adapter_support_matrix_covers_issue_904_boundaries() -> None:
    matrix = _matrix()

    assert matrix["schema_version"] == 1
    assert matrix["issue"] == 904
    audited = matrix["audited_boundaries"]
    assert isinstance(audited, list)
    assert REQUIRED_BOUNDARIES <= set(audited)


def test_adapter_support_matrix_entries_are_complete_and_unique() -> None:
    matrix = _matrix()
    allowed_tiers = set(matrix["allowed_tiers"])
    entries = _implementations()
    ids: list[str] = []

    for entry in entries:
        missing = REQUIRED_FIELDS - set(entry)
        assert not missing, f"{entry.get('id', '<unknown>')} missing fields: {sorted(missing)}"
        assert entry["tier"] in allowed_tiers
        ids.append(str(entry["id"]))

    assert len(ids) == len(set(ids)), "adapter support ids must be unique"


def test_adapter_support_matrix_points_at_real_implementation_symbols_and_docs() -> None:
    for entry in _implementations():
        source = ROOT / str(entry["source"])
        assert source.is_file(), f"missing source for {entry['id']}: {source.relative_to(ROOT)}"
        source_text = source.read_text(encoding="utf-8")
        assert str(entry["symbol"]) in source_text, (
            f"support entry {entry['id']} names symbol {entry['symbol']} "
            f"that is absent from {source.relative_to(ROOT)}"
        )

        docs = entry["docs"]
        assert isinstance(docs, list) and docs, f"{entry['id']} must name documentation"
        for doc in docs:
            path = ROOT / str(doc)
            assert path.is_file(), f"missing documentation for {entry['id']}: {doc}"


def test_reference_and_supported_entries_have_evidence_and_reference_is_free_baseline() -> None:
    for entry in _implementations():
        tier = entry["tier"]
        if tier in {"reference", "supported"}:
            evidence = entry["evidence"]
            assert isinstance(evidence, list) and evidence, (
                f"{entry['id']} is {tier} but has no conformance/test evidence mapping"
            )

        if tier == "reference":
            assert entry["mandatory_paid_service"] is False, (
                f"reference implementation {entry['id']} cannot require a paid service"
            )


def test_experimental_and_deprecated_entries_cannot_imply_unqualified_support() -> None:
    for entry in _implementations():
        compatibility = str(entry["compatibility"]).lower()
        if entry["tier"] == "experimental":
            assert any(
                marker in compatibility
                for marker in ("no production", "no supported", "not part", "implemented and tested")
            ), f"experimental entry {entry['id']} must explicitly limit its compatibility claim"
        if entry["tier"] == "deprecated":
            migration = str(entry["migration"])
            assert migration and migration.lower() != "n/a", (
                f"deprecated entry {entry['id']} requires an explicit migration/removal plan"
            )


def test_policy_document_mentions_every_first_party_support_entry() -> None:
    policy = POLICY_PATH.read_text(encoding="utf-8")
    for entry in _implementations():
        assert f"`{entry['id']}`" in policy, f"policy document omits {entry['id']}"
