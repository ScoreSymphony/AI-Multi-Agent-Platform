from __future__ import annotations

from pathlib import Path

from ai_multi_agent_platform.conformance import (
    ConformanceProfile,
    profile_scenarios,
    security_boundary_claims,
    security_boundary_pytest_nodes,
)

ROOT = Path(__file__).resolve().parents[3]

REQUIRED_SURFACES = {
    "control-plane",
    "capability-tool",
    "mcp",
    "browser-network",
    "terminal-process",
    "repository-git",
    "connector",
    "plugin",
    "application",
    "worker-dispatch",
    "marketplace",
    "automation",
    "import-export",
    "secret-config",
    "approval",
    "verification-review",
}

BOUNDARY_FIELDS = (
    "authentication",
    "authorization",
    "approval",
    "scope",
    "secrets",
    "audit",
    "cancellation_failure",
    "fail_closed",
    "public_errors",
    "canonical_authority",
)


def test_security_boundary_matrix_covers_every_claimed_side_effect_surface() -> None:
    claims = security_boundary_claims()

    assert {claim.surface_id for claim in claims} == REQUIRED_SURFACES
    assert len(claims) == len(REQUIRED_SURFACES)

    for claim in claims:
        assert claim.surface.strip()
        for field in BOUNDARY_FIELDS:
            value = getattr(claim, field)
            assert isinstance(value, str) and value.strip(), (
                f"{claim.surface_id} missing security boundary field {field}"
            )
        assert claim.evidence, f"{claim.surface_id} must retain executable evidence"


def test_security_boundary_matrix_evidence_nodes_are_current() -> None:
    nodes = security_boundary_pytest_nodes()

    assert nodes
    assert len(nodes) == len(set(nodes))
    for node in nodes:
        path_text, separator, test_name = node.partition("::")
        assert separator and test_name.startswith("test_"), f"evidence must select a test: {node}"
        path = ROOT / path_text
        assert path.is_file(), f"security evidence path is stale: {path_text}"
        source = path.read_text(encoding="utf-8")
        assert f"def {test_name}" in source, f"security evidence test is stale: {node}"


def test_release_profile_gates_on_the_complete_security_boundary_matrix() -> None:
    scenarios = {
        scenario.scenario_id: scenario
        for scenario in profile_scenarios(ConformanceProfile.RELEASE)
    }
    scenario = scenarios["SEC"]

    assert scenario.required is True
    assert scenario.command is not None
    assert tuple(scenario.command[-len(security_boundary_pytest_nodes()) :]) == (
        security_boundary_pytest_nodes()
    )
