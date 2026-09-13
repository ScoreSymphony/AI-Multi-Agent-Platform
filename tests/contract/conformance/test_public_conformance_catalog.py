from __future__ import annotations

from pathlib import Path

from ai_multi_agent_platform.conformance import ConformanceProfile, profile_scenarios

_DOCUMENTATION_PATH = Path("docs/PLATFORM_CONFORMANCE.md")
_WORKFLOW_PATH = Path(".github/workflows/conformance.yml")


def _documented_scenario_ids(documentation: str, expected: set[str]) -> set[str]:
    documented: set[str] = set()
    for line in documentation.splitlines():
        if not line.startswith("| "):
            continue
        first_cell = line.split("|", 2)[1].strip()
        scenario_id = first_cell.split(" — ", 1)[0].strip()
        if scenario_id in expected:
            documented.add(scenario_id)

    # Active scenarios are catalogued in tables. Retired stable IDs may instead
    # remain as explicit prose tombstones so they stay documented without being
    # presented as maintained compatibility evidence.
    for scenario_id in expected - documented:
        if f"Scenario ID {scenario_id} " in documentation:
            documented.add(scenario_id)

    return documented


def test_public_conformance_documentation_covers_release_registry() -> None:
    expected = {scenario.scenario_id for scenario in profile_scenarios(ConformanceProfile.RELEASE)}
    documentation = _DOCUMENTATION_PATH.read_text(encoding="utf-8")

    assert _documented_scenario_ids(documentation, expected) == expected


def test_public_conformance_documentation_references_real_workflow() -> None:
    documentation = _DOCUMENTATION_PATH.read_text(encoding="utf-8")

    assert _WORKFLOW_PATH.is_file()
    assert f"`{_WORKFLOW_PATH.as_posix()}`" in documentation
    assert ".github/workflows/platform-conformance.yml" not in documentation
