from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "release" / "generate_supply_chain_evidence.py"
RELEASE_INPUT_PATH = ROOT / "release" / "release-generation-input.example.json"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "release-manifest.yml"


def _load_module():
    spec = importlib.util.spec_from_file_location("generate_supply_chain_evidence", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_supply_chain_evidence_contains_python_and_frontend_dependencies(
    tmp_path: Path,
) -> None:
    module = _load_module()
    pyproject = tmp_path / "pyproject.toml"
    declared = tmp_path / "python-declared.txt"
    resolved = tmp_path / "python-resolved.txt"
    frontend = tmp_path / "package-lock.json"
    sbom = tmp_path / "sbom.spdx.json"

    pyproject.write_text(
        (
            '[project]\n'
            'name = "ai-multi-agent-platform"\n'
            'version = "1.0.0"\n'
            'license = { text = "MIT" }\n'
        ),
        encoding="utf-8",
    )
    declared.write_text(
        "jsonschema==4.26.0\ntzdata==2026.4; platform_system == 'Windows'\n",
        encoding="utf-8",
    )
    resolved.write_text(
        "ai-multi-agent-platform @ file:///checkout\njsonschema==4.26.0\n",
        encoding="utf-8",
    )
    frontend.write_text(
        json.dumps(
            {
                "packages": {
                    "": {"name": "frontend", "version": "1.0.0"},
                    "node_modules/react": {"version": "19.1.0"},
                    "node_modules/@scope/tool": {"name": "@scope/tool", "version": "2.3.4"},
                }
            }
        ),
        encoding="utf-8",
    )

    module.generate_release_sbom(
        source_commit="a" * 40,
        created_at="2026-09-20T16:00:00Z",
        pyproject=pyproject,
        python_declared=declared,
        python_resolved=resolved,
        frontend_lock=frontend,
        sbom_output=sbom,
    )

    document = json.loads(sbom.read_text(encoding="utf-8"))
    assert document["spdxVersion"] == "SPDX-2.3"
    assert document["documentNamespace"].endswith("/" + "a" * 40)
    packages = {(item["name"], item["versionInfo"]) for item in document["packages"]}
    assert ("ai-multi-agent-platform", "1.0.0") in packages
    assert ("jsonschema", "4.26.0") in packages
    assert ("tzdata", "2026.4") in packages
    assert ("react", "19.1.0") in packages
    assert ("@scope/tool", "2.3.4") in packages
    assert sum(name == "ai-multi-agent-platform" for name, _ in packages) == 1

    root = next(item for item in document["packages"] if item["name"] == "ai-multi-agent-platform")
    assert root["licenseDeclared"] == "MIT"
    relationships = document["relationships"]
    assert any(item["relationshipType"] == "DESCRIBES" for item in relationships)
    assert sum(item["relationshipType"] == "DEPENDS_ON" for item in relationships) == 4


def test_supply_chain_evidence_rejects_short_source_commit(tmp_path: Path) -> None:
    module = _load_module()

    try:
        module.generate_release_sbom(
            source_commit="abc123",
            created_at="2026-09-20T16:00:00Z",
            pyproject=tmp_path / "pyproject.toml",
            python_declared=tmp_path / "python-declared.txt",
            python_resolved=tmp_path / "python-resolved.txt",
            frontend_lock=tmp_path / "package-lock.json",
            sbom_output=tmp_path / "sbom.spdx.json",
        )
    except ValueError as exc:
        assert "full lowercase Git SHA" in str(exc)
    else:
        raise AssertionError("short source commit must fail closed")


def test_release_manifest_workflow_binds_signed_sbom_and_provenance() -> None:
    release_input = json.loads(RELEASE_INPUT_PATH.read_text(encoding="utf-8"))
    artifacts = {item["name"]: item["path"] for item in release_input["artifacts"]}

    assert release_input["sbom_ref"] == "REPLACE_WITH_SIGNED_SBOM_ATTESTATION_URL"
    assert release_input["provenance_ref"] == "REPLACE_WITH_SIGNED_PROVENANCE_ATTESTATION_URL"
    assert artifacts["platform-wheel"] == "../.release-evidence/platform.whl"
    assert artifacts["sbom.spdx.json"] == "../.release-evidence/sbom.spdx.json"
    assert artifacts["build-provenance.attestation.json"].endswith(
        "build-provenance.attestation.json"
    )
    assert artifacts["sbom.attestation.json"].endswith("sbom.attestation.json")

    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "id-token: write" in workflow
    assert "attestations: write" in workflow
    assert "uses: actions/attest@v4" in workflow
    assert "sbom-path: .release-evidence/sbom.spdx.json" in workflow
    assert "PROVENANCE_ATTESTATION_URL" in workflow
    assert "SBOM_ATTESTATION_URL" in workflow
    assert ".release-evidence/generation-input.resolved.json" in workflow
    assert ".release-evidence/SHA256SUMS" in workflow
