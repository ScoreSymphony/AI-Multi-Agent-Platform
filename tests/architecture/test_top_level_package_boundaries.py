from __future__ import annotations

import ast
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "docs" / "PACKAGE_BOUNDARIES.toml"
SOURCE_ROOT = ROOT / "src" / "ai_multi_agent_platform"
PLATFORM_PACKAGE = "ai_multi_agent_platform"

ALLOWED_KINDS = {
    "domain",
    "integration",
    "migration",
    "operations",
    "quality",
    "surface",
}
ALLOWED_DISPOSITIONS = {"keep", "consolidate", "compatibility"}
EXTERNAL_OWNERS = {"repository"}


def _manifest() -> dict[str, Any]:
    with MANIFEST_PATH.open("rb") as handle:
        return tomllib.load(handle)


def _package_entries() -> list[dict[str, Any]]:
    entries = _manifest().get("package")
    assert isinstance(entries, list), "PACKAGE_BOUNDARIES.toml must define [[package]] entries"
    return entries


def _actual_top_level_packages() -> set[str]:
    return {
        path.name
        for path in SOURCE_ROOT.iterdir()
        if path.is_dir() and (path / "__init__.py").is_file()
    }


def _package_parts_for_module(path: Path) -> tuple[str, ...]:
    parts = list(path.relative_to(SOURCE_ROOT).with_suffix("").parts)
    parts.pop()
    return tuple(parts)


def _import_targets(path: Path) -> tuple[tuple[int, str], ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    package_parts = _package_parts_for_module(path)
    targets: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            targets.extend((node.lineno, alias.name) for alias in node.names)
            continue
        if not isinstance(node, ast.ImportFrom):
            continue

        if node.level:
            parent_count = node.level - 1
            if parent_count > len(package_parts):
                continue
            base_parts = package_parts[: len(package_parts) - parent_count]
            if node.module:
                base_parts = (*base_parts, *node.module.split("."))
            base = ".".join((PLATFORM_PACKAGE, *base_parts))
        else:
            base = node.module or ""

        if not base:
            continue
        targets.append((node.lineno, base))
        targets.extend(
            (node.lineno, f"{base}.{alias.name}") for alias in node.names if alias.name != "*"
        )

    return tuple(targets)


def test_package_boundary_inventory_matches_root_namespace_exactly() -> None:
    entries = _package_entries()
    names = [entry.get("name") for entry in entries]

    assert all(isinstance(name, str) and name for name in names)
    assert len(names) == len(set(names)), "duplicate top-level package inventory entry"
    assert set(names) == _actual_top_level_packages()


def test_package_boundary_inventory_has_explicit_ownership_and_responsibility() -> None:
    entries = _package_entries()
    package_names = {entry["name"] for entry in entries}

    for entry in entries:
        name = entry["name"]
        kind = entry.get("kind")
        owner = entry.get("owner")
        disposition = entry.get("disposition")
        responsibility = entry.get("responsibility")

        assert kind in ALLOWED_KINDS, f"{name}: invalid package kind {kind!r}"
        assert disposition in ALLOWED_DISPOSITIONS, (
            f"{name}: invalid package disposition {disposition!r}"
        )
        assert owner in package_names | EXTERNAL_OWNERS, f"{name}: unknown owner {owner!r}"
        assert isinstance(responsibility, str) and len(responsibility.strip()) >= 20, (
            f"{name}: responsibility rationale is missing or too vague"
        )

        if kind == "migration":
            assert owner != name, f"{name}: migration packages require a different target owner"
            assert disposition in {"consolidate", "compatibility"}
        if disposition in {"consolidate", "compatibility"}:
            assert kind == "migration", (
                f"{name}: consolidation/compatibility dispositions must be explicit migrations"
            )


def test_migration_packages_do_not_reverse_canonical_dependency_direction() -> None:
    violations: list[str] = []

    for entry in _package_entries():
        if entry["kind"] != "migration":
            continue
        owner = entry["owner"]
        if owner in EXTERNAL_OWNERS:
            continue

        forbidden = f"{PLATFORM_PACKAGE}.{entry['name']}"
        owner_root = SOURCE_ROOT / owner
        for path in owner_root.rglob("*.py"):
            for lineno, target in _import_targets(path):
                if target == forbidden or target.startswith(f"{forbidden}."):
                    relative = path.relative_to(ROOT)
                    violations.append(
                        f"{relative}:{lineno}: canonical owner {owner!r} imports "
                        f"migration namespace {entry['name']!r} via {target!r}"
                    )

    assert not violations, (
        "canonical owners must not depend on their migration namespaces:\n" + "\n".join(violations)
    )


def test_confusing_package_families_have_explicit_non_overlapping_owners() -> None:
    packages = {entry["name"]: entry for entry in _package_entries()}

    assert packages["deployment"]["kind"] == "operations"
    assert packages["deployment"]["owner"] == "repository"

    assert packages["distributed"]["kind"] == "domain"
    assert packages["distributed"]["owner"] == "distributed"

    assert packages["distribution"]["kind"] == "domain"
    assert packages["distribution"]["owner"] == "distribution"

    assert packages["high_availability"]["kind"] == "migration"
    assert packages["high_availability"]["owner"] == "distributed"


def test_task_reassignment_is_owned_by_task_management() -> None:
    packages = {entry["name"]: entry for entry in _package_entries()}

    assert packages["task_management"]["kind"] == "domain"
    assert packages["task_management"]["owner"] == "task_management"
    assert packages["task_reassignment"]["kind"] == "migration"
    assert packages["task_reassignment"]["owner"] == "task_management"


def test_task_reassignment_compatibility_import_preserves_public_objects() -> None:
    from ai_multi_agent_platform import task_reassignment as compatibility
    from ai_multi_agent_platform.task_management import reassignment as canonical

    public_names = (
        "DefaultTaskProjectCompatibilityPolicy",
        "PreparedTaskProjectMove",
        "TaskProjectCompatibilityPolicy",
        "TaskProjectMoveRequest",
        "TaskProjectReassignmentService",
    )
    for name in public_names:
        assert getattr(compatibility, name) is getattr(canonical, name)
