import json

from ai_multi_agent_platform.release.cli import main
from ai_multi_agent_platform.upgrade.versioning import current_release_versions

CANDIDATE_REVISION = "b" * 40


def test_upstream_adoption_check_is_revision_bound_and_read_only(tmp_path, capsys) -> None:
    inventory_path = tmp_path / "compatibility.json"
    inventory_document = {
        "schema_version": "2",
        "platform_release": current_release_versions().platform_release,
        "versions": current_release_versions().to_dict(),
        "generated_from": "test",
        "last_reviewed_at": "2026-09-01T00:00:00Z",
        "components": [
            {
                "component": "runtime",
                "source_url": "https://example.invalid/runtime",
                "revision": "a" * 40,
                "status": "tested",
                "integration_mode": "optional adapter",
                "boundary": "Executor",
                "license": "MIT",
                "last_checked_at": "2026-09-01T00:00:00Z",
                "latest_known_revision": "a" * 40,
                "update_risk": "high",
                "local_modifications": False,
                "patches": [],
                "notes": [],
            }
        ],
    }
    inventory_path.write_text(json.dumps(inventory_document), encoding="utf-8")

    observations_path = tmp_path / "observations.json"
    observations_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "observed_at": "2026-09-06T00:00:00Z",
                "components": [
                    {
                        "component": "runtime",
                        "source_url": "https://example.invalid/runtime",
                        "revision": CANDIDATE_REVISION,
                        "license": "MIT",
                        "classifications": ["feature"],
                        "patch_conflicts": [],
                        "validation": {
                            "adapter_contract_tests": "passed",
                            "eval_regression": "passed",
                            "security": "passed",
                            "compatibility_review": "passed",
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "component": "runtime",
                "candidate_revision": CANDIDATE_REVISION,
                "gates": {
                    name: {
                        "status": "passed",
                        "kind": "workflow_run",
                        "ref": f"workflow:run/{name}",
                        "digest": None,
                    }
                    for name in (
                        "adapter_contract_tests",
                        "eval_regression",
                        "security",
                        "compatibility_review",
                    )
                },
            }
        ),
        encoding="utf-8",
    )

    original_inventory = inventory_path.read_text(encoding="utf-8")
    exit_code = main(
        [
            "upstream-adoption-check",
            "--inventory",
            str(inventory_path),
            "--observations",
            str(observations_path),
            "--component",
            "runtime",
            "--evidence",
            str(evidence_path),
            "--compatibility-status",
            "tested",
            "--reviewed-at",
            "2026-09-06T00:00:00Z",
            "--json",
        ]
    )

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["candidate"]["candidate_revision"] == CANDIDATE_REVISION
    assert payload["validation_evidence"]["candidate_revision"] == CANDIDATE_REVISION
    assert (
        payload["resulting_compatibility_inventory"]["components"][0]["revision"]
        == CANDIDATE_REVISION
    )
    assert inventory_path.read_text(encoding="utf-8") == original_inventory
