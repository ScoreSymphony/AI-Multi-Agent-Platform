import hashlib
import json
from dataclasses import replace

import pytest

from ai_multi_agent_platform.release import (
    COMMIT_BOUND_RELEASE_GATES,
    REQUIRED_RELEASE_GATES,
    ArtifactSource,
    DependencySetKind,
    DependencySetSource,
    GateStatus,
    ReleaseEvidence,
    ReleaseEvidenceKind,
    ReleaseGate,
    ReleaseGenerationError,
    ReleaseGenerationInputs,
    ReleaseKind,
    generate_release_manifest,
    generate_release_manifest_from_file,
    load_compatibility_inventory,
    load_release_manifest,
    write_release_manifest,
)

SOURCE_COMMIT = "a" * 40
NOW = "2026-09-06T18:30:00Z"


def _gates() -> tuple[ReleaseGate, ...]:
    return tuple(
        ReleaseGate(
            name=name,
            status=GateStatus.PASSED,
            evidence=ReleaseEvidence(
                kind=ReleaseEvidenceKind.WORKFLOW_RUN,
                ref=f"workflow:run/{name}",
                source_commit=SOURCE_COMMIT if name in COMMIT_BOUND_RELEASE_GATES else None,
            ),
            required=True,
        )
        for name in sorted(REQUIRED_RELEASE_GATES)
    )


def _inputs(tmp_path) -> ReleaseGenerationInputs:
    dependency = tmp_path / "requirements.lock"
    artifact = tmp_path / "platform.whl"
    dependency.write_text("jsonschema==4.26.0\n", encoding="utf-8")
    artifact.write_bytes(b"deterministic-artifact")
    return ReleaseGenerationInputs(
        release_kind=ReleaseKind.PATCH,
        created_at=NOW,
        release_notes_ref=f"git:{SOURCE_COMMIT}:CHANGELOG.md",
        sbom_ref="artifact:sbom.spdx.json",
        provenance_ref="attestation:release-provenance.json",
        dependency_sets=(
            DependencySetSource(
                name="python-lock",
                ecosystem="python",
                kind=DependencySetKind.LOCKFILE,
                path=dependency,
                source_ref=f"git:{SOURCE_COMMIT}:requirements.lock",
            ),
        ),
        artifacts=(ArtifactSource(name="platform-wheel", path=artifact),),
        gates=_gates(),
    )


def test_generation_is_deterministic_and_binds_canonical_release_state(tmp_path) -> None:
    inventory = load_compatibility_inventory()
    inputs = _inputs(tmp_path)

    first = generate_release_manifest(
        source_commit=SOURCE_COMMIT,
        inputs=inputs,
        inventory=inventory,
    )
    second = generate_release_manifest(
        source_commit=SOURCE_COMMIT,
        inputs=inputs,
        inventory=inventory,
    )

    assert first.to_dict() == second.to_dict()
    assert first.versions.to_dict() == inventory.versions.to_dict()
    assert [item.revision for item in first.upstreams] == [
        item.revision for item in inventory.entries
    ]
    litellm = next(item for item in first.upstreams if item.component == "LiteLLM")
    assert litellm.revision_kind == "tag"
    expected = "sha256:" + hashlib.sha256(b"deterministic-artifact").hexdigest()
    assert first.artifact_hashes["platform-wheel"] == expected


def test_generation_rejects_stale_compatibility_version_vector(tmp_path) -> None:
    inventory = load_compatibility_inventory()
    stale = replace(
        inventory,
        versions=replace(inventory.versions, worker_protocol="999"),
    )

    with pytest.raises(ReleaseGenerationError, match="VersionSnapshot"):
        generate_release_manifest(
            source_commit=SOURCE_COMMIT,
            inputs=_inputs(tmp_path),
            inventory=stale,
        )


def test_file_generation_expands_source_commit_and_round_trips_schema_v2(tmp_path) -> None:
    dependency = tmp_path / "requirements.lock"
    artifact = tmp_path / "platform.whl"
    dependency.write_text("jsonschema==4.26.0\n", encoding="utf-8")
    artifact.write_bytes(b"artifact")
    gate_documents = []
    for gate in _gates():
        evidence = gate.evidence.to_dict()
        if evidence["source_commit"] is not None:
            evidence["source_commit"] = "${SOURCE_COMMIT}"
        gate_documents.append(
            {
                "name": gate.name,
                "status": gate.status.value,
                "evidence": evidence,
                "required": gate.required,
            }
        )
    input_path = tmp_path / "release-input.json"
    input_path.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "release_kind": "patch",
                "created_at": NOW,
                "release_notes_ref": "git:${SOURCE_COMMIT}:CHANGELOG.md",
                "sbom_ref": "artifact:sbom.spdx.json",
                "provenance_ref": "attestation:release-provenance.json",
                "dependency_sets": [
                    {
                        "name": "python-lock",
                        "ecosystem": "python",
                        "kind": "lockfile",
                        "path": "requirements.lock",
                        "source_ref": "git:${SOURCE_COMMIT}:requirements.lock",
                    }
                ],
                "artifacts": [{"name": "platform-wheel", "path": "platform.whl"}],
                "gates": gate_documents,
            }
        ),
        encoding="utf-8",
    )

    manifest = generate_release_manifest_from_file(
        source_commit=SOURCE_COMMIT,
        input_path=input_path,
    )
    output = tmp_path / "release-manifest.json"
    write_release_manifest(manifest, output)
    loaded = load_release_manifest(output)

    assert loaded.to_dict() == manifest.to_dict()
    assert loaded.source_commit == SOURCE_COMMIT
    assert loaded.release_notes_ref == f"git:{SOURCE_COMMIT}:CHANGELOG.md"
