from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs" / "CONTRIBUTOR_ARCHITECTURE.md"
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")

REQUIRED_HEADINGS = (
    "## Platform in one page",
    "## Primary domain objects",
    "## Lifecycle of a representative task",
    "## Repository and package map",
    "## Where do I change X?",
    "## Extension rules contributors must not violate",
    "## Common change recipes",
    "## Validation map",
    "## Documentation map: read next",
)


def _guide_text() -> str:
    return GUIDE.read_text(encoding="utf-8")


def test_contributor_architecture_guide_has_required_navigation_sections() -> None:
    text = _guide_text()

    for heading in REQUIRED_HEADINGS:
        assert heading in text, f"missing contributor-guide section: {heading}"

    for change in (
        "Agent / Agent Team",
        "model provider",
        "Capability / Tool",
        "Control Plane resource",
        "frontend feature",
        "authentication, authorization or approval policy",
        "execution backend",
        "connector / external integration",
        "Task / Run lifecycle",
        "schema / public API",
        "distributed behavior",
    ):
        assert change in text, f"missing change-navigation entry: {change}"


def test_contributor_architecture_guide_local_links_resolve() -> None:
    text = _guide_text()
    broken: list[str] = []

    for raw_target in MARKDOWN_LINK.findall(text):
        target = raw_target.split("#", 1)[0]
        if not target or target.startswith(("http://", "https://", "mailto:")):
            continue

        resolved = (GUIDE.parent / target).resolve()
        try:
            resolved.relative_to(ROOT.resolve())
        except ValueError:
            broken.append(f"{raw_target} -> outside repository")
            continue

        if not resolved.exists():
            broken.append(f"{raw_target} -> {resolved.relative_to(ROOT)}")

    assert not broken, "contributor-guide links must resolve:\n" + "\n".join(broken)


def test_contributor_architecture_guide_points_to_canonical_guards() -> None:
    text = _guide_text()

    for canonical_reference in (
        "ARCHITECTURE_PRINCIPLES.md",
        "DOMAIN_MODEL.md",
        "CONTRACTS.md",
        "KERNEL.md",
        "PACKAGE_BOUNDARIES.toml",
        "control_plane/extensions.py",
        "frontend/src/api/transport.ts",
        "test_top_level_package_boundaries.py",
        "test_explicit_module_composition.py",
    ):
        assert canonical_reference in text
