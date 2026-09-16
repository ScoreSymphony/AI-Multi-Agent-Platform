from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from ai_multi_agent_platform.release import DependencySetKind

ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = ROOT / "scripts" / "release" / "python_declared_dependencies.py"
PYPROJECT_PATH = ROOT / "pyproject.toml"
RELEASE_INPUT_PATH = ROOT / "release" / "release-generation-input.example.json"
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "release-manifest.yml"
UPSTREAMS_PATH = ROOT / "docs" / "UPSTREAMS.md"
TZDATA_PROVENANCE_PATH = ROOT / "upstream" / "tzdata.yaml"


def _load_inventory_module():
    spec = importlib.util.spec_from_file_location("python_declared_dependencies", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_declared_python_inventory_preserves_windows_environment_marker(tmp_path) -> None:
    module = _load_inventory_module()
    output = tmp_path / "python-declared.txt"

    module.write_declared_dependencies(PYPROJECT_PATH, output)

    requirements = output.read_text(encoding="utf-8").splitlines()
    assert "jsonschema==4.26.0" in requirements
    assert "tzdata==2026.4; platform_system == 'Windows'" in requirements


def test_release_evidence_binds_declared_and_resolved_python_dependency_sets() -> None:
    release_input = json.loads(RELEASE_INPUT_PATH.read_text(encoding="utf-8"))
    python_sets = {
        item["kind"]: item
        for item in release_input["dependency_sets"]
        if item["ecosystem"] == "python"
    }

    assert DependencySetKind.DECLARED_SET.value == "declared_set"
    assert python_sets["declared_set"]["name"] == "python-declared"
    assert python_sets["declared_set"]["path"] == "../.release-evidence/python-declared.txt"
    assert python_sets["resolved_set"]["name"] == "python-resolved"

    workflow = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "scripts/release/python_declared_dependencies.py" in workflow
    assert ".release-evidence/python-declared.txt" in workflow


def test_tzdata_provenance_and_documentation_match_runtime_pin() -> None:
    provenance = TZDATA_PROVENANCE_PATH.read_text(encoding="utf-8")
    upstreams = UPSTREAMS_PATH.read_text(encoding="utf-8")

    assert 'pinned_revision: "2026.4"' in provenance
    assert 'verified_license: "Apache-2.0"' in provenance
    assert "platform_system == 'Windows'" in provenance
    assert "### tzdata" in upstreams
    assert "`==2026.4; platform_system == 'Windows'`" in upstreams
