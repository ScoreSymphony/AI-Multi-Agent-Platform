from __future__ import annotations

import ast
import tomllib
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "src" / "ai_multi_agent_platform"
PACKAGE_BOUNDARIES = ROOT / "docs" / "PACKAGE_BOUNDARIES.toml"
FEATURE_CLASSIFICATION = ROOT / "docs" / "FEATURE_CLASSIFICATION.toml"
PLATFORM_PACKAGE = "ai_multi_agent_platform"
APPLICATION_OWNER = f"{PLATFORM_PACKAGE}.applications"
LIFECYCLE_OWNERSHIP_MODULES = frozenset(
    {
        "audit",
        "control_plane",
        "local_process_runtime",
        "log_control_plane",
        "repository",
        "runtime",
        "service",
        "sqlite_repository",
        "workspace_execution",
    }
)
LIFECYCLE_ROOT_EXPORTS = frozenset(
    {
        "ApplicationAuditLog",
        "ApplicationInstanceResourceService",
        "ApplicationLifecycleService",
        "ApplicationLogResourceService",
        "ApplicationRepository",
        "ApplicationResourceService",
        "ApplicationRuntime",
        "ApplicationRuntimeRegistry",
        "InMemoryApplicationRepository",
        "LocalApplicationWorkspaceBinder",
        "LocalProcessApplicationRuntime",
        "SqliteApplicationAuditStore",
        "SqliteApplicationRepository",
        "application_audit_control_plane_module",
        "application_control_plane_module",
        "application_log_control_plane_module",
        "register_application_audit_control_plane",
        "register_application_control_plane",
        "register_application_log_control_plane",
    }
)
LIFECYCLE_FORBIDDEN_CONSUMERS = (
    "application_distribution",
    "distribution",
    "deployment",
    "distributed",
    "security",
)


def _load(path: Path) -> dict[str, Any]:
    with path.open("rb") as handle:
        return tomllib.load(handle)


def _package_parts_for_module(path: Path) -> tuple[str, ...]:
    parts = list(path.relative_to(SOURCE_ROOT).with_suffix("").parts)
    parts.pop()
    return tuple(parts)


def _resolve_import_from(path: Path, node: ast.ImportFrom) -> str:
    if not node.level:
        return node.module or ""

    package_parts = _package_parts_for_module(path)
    parent_count = node.level - 1
    if parent_count > len(package_parts):
        return ""

    base_parts = package_parts[: len(package_parts) - parent_count]
    if node.module:
        base_parts = (*base_parts, *node.module.split("."))
    return ".".join((PLATFORM_PACKAGE, *base_parts))


def _is_lifecycle_module(target: str) -> bool:
    return any(
        target == f"{APPLICATION_OWNER}.{module}"
        or target.startswith(f"{APPLICATION_OWNER}.{module}.")
        for module in LIFECYCLE_OWNERSHIP_MODULES
    )


def _application_lifecycle_imports(path: Path) -> tuple[tuple[int, str], ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == APPLICATION_OWNER or _is_lifecycle_module(alias.name):
                    violations.append((node.lineno, alias.name))
            continue
        if not isinstance(node, ast.ImportFrom):
            continue

        base = _resolve_import_from(path, node)
        if _is_lifecycle_module(base):
            violations.append((node.lineno, base))
            continue

        if base == APPLICATION_OWNER:
            for alias in node.names:
                if alias.name == "*" or alias.name in LIFECYCLE_ROOT_EXPORTS:
                    violations.append((node.lineno, f"{base}.{alias.name}"))
            continue

        for alias in node.names:
            target = f"{base}.{alias.name}" if base else alias.name
            if target == APPLICATION_OWNER or _is_lifecycle_module(target):
                violations.append((node.lineno, target))

    return tuple(violations)


def test_managed_application_lifecycle_has_a_distinct_canonical_owner() -> None:
    entries = _load(PACKAGE_BOUNDARIES)["package"]
    packages = {entry["name"]: entry for entry in entries}

    assert packages["applications"]["kind"] == "domain"
    assert packages["applications"]["owner"] == "applications"
    assert packages["application_distribution"]["owner"] == "application_distribution"
    assert packages["distribution"]["owner"] == "distribution"
    assert packages["distributed"]["owner"] == "distributed"
    assert packages["deployment"]["owner"] == "repository"


def test_adjacent_authorities_cannot_import_application_lifecycle_ownership() -> None:
    violations: list[str] = []

    for package in LIFECYCLE_FORBIDDEN_CONSUMERS:
        package_root = SOURCE_ROOT / package
        for path in package_root.rglob("*.py"):
            for lineno, target in _application_lifecycle_imports(path):
                relative = path.relative_to(ROOT)
                violations.append(f"{relative}:{lineno}: imports {target}")

    assert not violations, (
        "Application lifecycle ownership must remain in 'applications' or outer adapters; "
        "adjacent distribution/deployment/distributed/security authorities may reuse generic "
        "contracts but must not own Application lifecycle implementations:\n"
        + "\n".join(violations)
    )


def test_application_adapters_are_classified_as_beta_optional_advanced() -> None:
    features = _load(FEATURE_CLASSIFICATION)["feature"]
    feature = next(item for item in features if item["id"] == "application-adapters")

    assert feature["owners"] == ["applications"]
    assert feature["role"] == "optional_advanced"
    assert feature["stability"] == "beta"
    assert feature["user_signaling"] == "documentation_and_ui_contextual"
