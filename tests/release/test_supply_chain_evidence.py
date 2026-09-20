from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "release" / "generate_supply_chain_evidence.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("generate_supply_chain_evidence", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_supply_chain_evidence_binds_source_dependencies_and_artifacts(tmp_path: Path) -> None:
    module = _load_module()
    pyproject = tmp_path / "pyproject.toml"
    wheel = tmp_path / "platform.whl"
    declared = tmp_path / "python-declared.txt"
    resolved = tmp_path / "python-resolved.txt"
    frontend = tmp_path / "package-lock.json"
    sbom = tmp_path / "sbom.spdx.json"
    provenance = tmp_path / "release-provenance.json"

    pyproject.write_text(
        '[project]\nname = "ai-multi-agent-platform"\nversion = "1.0.0"\n',
        encoding="utf-8",
    )
    wheel.write_bytes(b"wheel")
    declared.write_text("jsonschema==4.26.0\n", encoding="utf-8")
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

    module.generate_release_supply_chain_evidence(
        source_commit="a" * 40,
        created_at="2026-09-20T16:00:00Z",
        pyproject=pyproject,
        platform_wheel=wheel,
        python_declared=declared,
        python_resolved=resolved,
        frontend_lock=frontend,
        sbom_output=sbom,
        provenance_output=provenance,
    )

    sbom_document = json.loads(sbom.read_text(encoding="utf-8"))
    assert sbom_document["spdxVersion"] == "SPDX-2.3"
    assert sbom_document["documentNamespace"].endswith("/" + "a" * 40)
    packages = {(item["name"], item["versionInfo"]) for item in sbom_document["packages"]}
    assert ("ai-multi-agent-platform", "1.0.0") in packages
    assert ("jsonschema", "4.26.0") in packages
    assert ("react", "19.1.0") in packages
    assert ("@scope/tool", "2.3.4") in packages

    provenance_document = json.loads(provenance.read_text(encoding="utf-8"))
    assert provenance_document["_type"] == "https://in-toto.io/Statement/v1"
    assert provenance_document["predicate"]["source"]["commit"] == "a" * 40
    assert provenance_document["predicate"]["release"]["version"] == "1.0.0"
    subjects = {item["name"]: item["digest"]["sha256"] for item in provenance_document["subject"]}
    assert set(subjects) == {"platform.whl", "sbom.spdx.json"}
    assert all(len(value) == 64 for value in subjects.values())
    materials = {
        item["uri"]: item["digest"]["sha256"]
        for item in provenance_document["predicate"]["materials"]
    }
    assert set(materials) == {
        "pyproject.toml",
        "python-declared.txt",
        "python-resolved.txt",
        "frontend/package-lock.json",
    }


def test_supply_chain_evidence_rejects_short_source_commit(tmp_path: Path) -> None:
    module = _load_module()

    try:
        module.generate_release_supply_chain_evidence(
            source_commit="abc123",
            created_at="2026-09-20T16:00:00Z",
            pyproject=tmp_path / "pyproject.toml",
            platform_wheel=tmp_path / "platform.whl",
            python_declared=tmp_path / "python-declared.txt",
            python_resolved=tmp_path / "python-resolved.txt",
            frontend_lock=tmp_path / "package-lock.json",
            sbom_output=tmp_path / "sbom.spdx.json",
            provenance_output=tmp_path / "release-provenance.json",
        )
    except ValueError as exc:
        assert "full lowercase Git SHA" in str(exc)
    else:
        raise AssertionError("short source commit must fail closed")
