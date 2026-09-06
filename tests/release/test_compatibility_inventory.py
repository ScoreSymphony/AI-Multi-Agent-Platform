import json
import re
import tomllib
from importlib.resources import files
from pathlib import Path

from ai_multi_agent_platform.adapters.hermes import HERMES_PINNED_REVISION
from ai_multi_agent_platform.upgrade.versioning import current_release_versions

ROOT = Path(__file__).resolve().parents[2]
COMPATIBILITY_PATH = ROOT / "release" / "compatibility.json"
CI_PATH = ROOT / ".github" / "workflows" / "ci.yml"
PYPROJECT_PATH = ROOT / "pyproject.toml"
LITELLM_INTEGRATION_TEST_PATH = ROOT / "tests" / "test_issue_11_litellm_pinned_integration.py"
TRACKED_UPSTREAMS = (
    ROOT / "upstream" / "hermes-agent.yaml",
    ROOT / "upstream" / "forge-ai-agent-vps.yaml",
    ROOT / "upstream" / "litellm.yaml",
)


def _quoted_field(path: Path, field: str) -> str:
    pattern = re.compile(rf'^{re.escape(field)}:\s*"([^"]+)"\s*$', re.MULTILINE)
    match = pattern.search(path.read_text(encoding="utf-8"))
    assert match is not None, f"missing {field} in {path}"
    return match.group(1)


def _governed_pin(path: Path) -> tuple[str, str]:
    return (
        _quoted_field(path, "canonical_upstream"),
        _quoted_field(path, "pinned_revision"),
    )


def test_compatibility_matrix_matches_governed_upstream_pins() -> None:
    document = json.loads(COMPATIBILITY_PATH.read_text(encoding="utf-8"))
    components = document["components"]
    by_source = {item["source_url"]: item for item in components}

    for upstream_path in TRACKED_UPSTREAMS:
        source, pinned_revision = _governed_pin(upstream_path)
        assert source in by_source
        assert by_source[source]["revision"] == pinned_revision


def test_runtime_and_ci_pins_match_governed_upstream_revisions() -> None:
    workflow = CI_PATH.read_text(encoding="utf-8")

    hermes_source, hermes_revision = _governed_pin(ROOT / "upstream" / "hermes-agent.yaml")
    assert HERMES_PINNED_REVISION == hermes_revision
    assert f"repository: {hermes_source.removeprefix('https://github.com/')}" in workflow
    assert f"ref: {hermes_revision}" in workflow
    assert f"HERMES_UPSTREAM_REVISION: {hermes_revision}" in workflow

    forge_source, forge_revision = _governed_pin(ROOT / "upstream" / "forge-ai-agent-vps.yaml")
    assert f"repository: {forge_source.removeprefix('https://github.com/')}" in workflow
    assert f"ref: {forge_revision}" in workflow


def test_litellm_package_and_integration_test_pin_match_governance() -> None:
    _, governed_revision = _governed_pin(ROOT / "upstream" / "litellm.yaml")
    governed_tag = governed_revision.split("/", maxsplit=1)[0].strip()
    governed_version = governed_tag.removeprefix("v")

    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    optional_dependencies = pyproject["project"]["optional-dependencies"]
    assert optional_dependencies["litellm"] == [f"litellm=={governed_version}"]

    integration_test = LITELLM_INTEGRATION_TEST_PATH.read_text(encoding="utf-8")
    assert f'PINNED_LITELLM_VERSION = "{governed_version}"' in integration_test


def test_compatibility_matrix_binds_complete_canonical_version_vector() -> None:
    document = json.loads(COMPATIBILITY_PATH.read_text(encoding="utf-8"))
    assert document["schema_version"] == "2"
    assert document["platform_release"] == document["versions"]["platform_release"]
    assert document["versions"] == current_release_versions().to_dict()


def test_packaged_compatibility_inventory_matches_repository_snapshot() -> None:
    repository = json.loads(COMPATIBILITY_PATH.read_text(encoding="utf-8"))
    packaged = json.loads(
        files("ai_multi_agent_platform.release")
        .joinpath("compatibility.json")
        .read_text(encoding="utf-8")
    )
    assert packaged == repository


def test_compatibility_matrix_contains_operator_query_fields() -> None:
    document = json.loads(COMPATIBILITY_PATH.read_text(encoding="utf-8"))
    versions = document["versions"]
    for name in (
        "platform_release",
        "domain_schema",
        "api",
        "migration_revision",
        "plugin_manifest",
        "portable_format",
        "template_schema",
        "backup_format",
        "worker_protocol",
        "message_protocol",
        "adapter_versions",
        "plugin_interface_versions",
    ):
        assert name in versions

    for component in document["components"]:
        assert component["license"]
        assert component["integration_mode"]
        assert component["last_checked_at"]
        assert component["latest_known_revision"]
        assert component["update_risk"] in {"low", "medium", "high"}
        assert isinstance(component["local_modifications"], bool)
        assert isinstance(component["patches"], list)


def test_release_compatibility_metadata_has_no_floating_latest_pin() -> None:
    raw = COMPATIBILITY_PATH.read_text(encoding="utf-8").lower()
    assert '"revision": "latest"' not in raw
    assert ":latest" not in raw
