from __future__ import annotations

import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

REMOVED_RUNTIME_PATHS = (
    "src/ai_multi_agent_platform/adapters/forge.py",
    "src/ai_multi_agent_platform/adapters/forge_http.py",
    "docs/integrations/FORGE_ADAPTER.md",
    "tests/contract/execution/test_forge_executor.py",
    "tests/contract/forge/test_forge_optionality.py",
    "tests/integration/forge/test_forge_http.py",
    "tests/integration/forge/test_sidecar.py",
    "tests/regression/forge/test_forge_kernel_regressions.py",
)


def test_forge_executable_surface_is_absent() -> None:
    for relative in REMOVED_RUNTIME_PATHS:
        assert not (ROOT / relative).exists(), f"retired Forge path returned: {relative}"


def test_forge_has_no_active_ci_or_external_conformance_lane() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    guard = (ROOT / ".github/workflows/repository-quality.yml").read_text(encoding="utf-8")
    external = (ROOT / "scripts/ci/issue46_external_profile.py").read_text(encoding="utf-8")

    assert "forge-sidecar-integration" not in ci
    assert "FORGE_SIDECAR_" not in ci
    assert "ScoreSymphony/AI-Agent-VPS" not in ci
    assert "executor-sidecar" not in ci
    assert "FORGE_SIDECAR_" not in external
    assert "def _forge" not in external

    # main still requires the historical check context. The consolidated quality
    # guard may preserve that name and refer to retired tokens in negative grep
    # assertions, but it must never recreate a Forge runtime/sidecar lane.
    assert "forge-sidecar-integration" in guard
    assert ".upstream/forge" not in guard
    assert "FORGE_EXECUTOR_" not in guard
    assert "cargo build" not in guard
    assert "http://127.0.0.1:8787" not in guard
    assert '! grep -Fq "ScoreSymphony/AI-Agent-VPS" .github/workflows/ci.yml' in guard
    assert '! grep -Fq "FORGE_SIDECAR_" .github/workflows/ci.yml' in guard
    assert '! grep -Fq "executor-sidecar" .github/workflows/ci.yml' in guard


def test_forge_is_not_a_first_party_or_release_compatibility_claim() -> None:
    support = tomllib.loads((ROOT / "docs/ADAPTER_SUPPORT_MATRIX.toml").read_text(encoding="utf-8"))
    implementation_ids = {entry["id"] for entry in support["implementation"]}
    assert "executor.forge" not in implementation_ids

    compatibility = json.loads((ROOT / "release/compatibility.json").read_text(encoding="utf-8"))
    sources = {entry["source_url"] for entry in compatibility["components"]}
    assert "https://github.com/ScoreSymphony/AI-Agent-VPS" not in sources


def test_forge_provenance_is_historical_and_removed() -> None:
    provenance = (ROOT / "upstream/forge-ai-agent-vps.yaml").read_text(encoding="utf-8")
    assert "status: removed" in provenance
    assert "Historical provenance" in provenance
    assert "No current Forge compatibility claim exists" in provenance
