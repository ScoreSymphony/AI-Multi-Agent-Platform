from __future__ import annotations

import json
from pathlib import Path

from ai_multi_agent_platform.release.cli import main

ROOT = Path(__file__).resolve().parents[2]
OLD_HERMES_REVISION = "63279301bcbdc185c1b07b98a9312eb0c862f26d"
CANDIDATE_REVISION = "2237be355906fbe6065ce1815711eee52b2d646e"


def test_hermes_v0_21_1_adoption_evidence_reproduces_pass_update(
    tmp_path: Path,
    capsys,
) -> None:
    current_inventory = json.loads(
        (ROOT / "release" / "compatibility.json").read_text(encoding="utf-8")
    )
    pre_adoption_inventory = json.loads(json.dumps(current_inventory))
    hermes = next(
        component
        for component in pre_adoption_inventory["components"]
        if component["component"] == "Hermes Agent"
    )
    hermes["revision"] = OLD_HERMES_REVISION
    hermes["latest_known_revision"] = OLD_HERMES_REVISION
    hermes["last_checked_at"] = "2026-09-03T00:00:00Z"

    inventory_path = tmp_path / "pre-adoption-compatibility.json"
    inventory_path.write_text(
        json.dumps(pre_adoption_inventory, indent=2) + "\n",
        encoding="utf-8",
    )
    original_inventory = inventory_path.read_text(encoding="utf-8")

    exit_code = main(
        [
            "upstream-adoption-check",
            "--inventory",
            str(inventory_path),
            "--observations",
            str(ROOT / "release" / "hermes-v0.21.1-observation.json"),
            "--component",
            "Hermes Agent",
            "--evidence",
            str(ROOT / "release" / "hermes-v0.21.1-validation-evidence.json"),
            "--compatibility-status",
            "tested",
            "--reviewed-at",
            "2026-09-11T00:00:00Z",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidate"]["current_revision"] == OLD_HERMES_REVISION
    assert payload["candidate"]["candidate_revision"] == CANDIDATE_REVISION
    assert payload["validation_evidence"]["candidate_revision"] == CANDIDATE_REVISION
    resulting_hermes = next(
        component
        for component in payload["resulting_compatibility_inventory"]["components"]
        if component["component"] == "Hermes Agent"
    )
    assert resulting_hermes["revision"] == CANDIDATE_REVISION
    assert resulting_hermes["latest_known_revision"] == CANDIDATE_REVISION
    assert inventory_path.read_text(encoding="utf-8") == original_inventory
