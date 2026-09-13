from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL_PLANE = ROOT / "src" / "ai_multi_agent_platform" / "control_plane"

# #982 removes inheritance as a *domain ownership/composition* mechanism. It does
# not outlaw every use of implementation inheritance (the issue explicitly keeps
# ordinary implementation inheritance out of scope). Keep the few pre-existing,
# reviewed implementation compositions explicit so a new ControlPlane MRO stack
# cannot be introduced silently while the remaining domain layers are migrated.
_ALLOWED_IMPLEMENTATION_MULTIPLE_INHERITANCE = {
    ("hardened_automation_api.py", "ControlPlane"): frozenset(
        {"_AutomationControlPlane", "_RegisteredSearchControlPlane"}
    ),
    ("workspace_task_management_api.py", "ControlPlane"): frozenset(
        {
            "RepositoryRunProvenanceMixin",
            "AuthorizationBoundaryHardeningMixin",
            "_RunWorkspaceControlPlane",
            "_TaskManagementControlPlane",
        }
    ),
    ("workspace_task_management_api.py", "ControlPlaneHTTP"): frozenset(
        {"_RunWorkspaceControlPlaneHTTP", "_TaskManagementControlPlaneHTTP"}
    ),
}


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class(path: Path, name: str) -> ast.ClassDef:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{path.relative_to(ROOT)} does not define {name}")


def _base_name(base: ast.expr) -> str:
    return ast.unparse(base)


def test_control_plane_domain_facades_do_not_add_unreviewed_multiple_inheritance() -> None:
    """A domain must not regain implicit ownership composition through Python MRO."""

    violations: list[str] = []
    seen_allowlisted: set[tuple[str, str]] = set()
    for path in sorted(CONTROL_PLANE.glob("*.py")):
        for node in _tree(path).body:
            if not (
                isinstance(node, ast.ClassDef)
                and node.name in {"ControlPlane", "ControlPlaneHTTP"}
                and len(node.bases) > 1
            ):
                continue

            key = (path.name, node.name)
            actual = frozenset(_base_name(base) for base in node.bases)
            expected = _ALLOWED_IMPLEMENTATION_MULTIPLE_INHERITANCE.get(key)
            if expected is None:
                violations.append(
                    f"{path.relative_to(ROOT)}:{node.lineno} adds an unreviewed "
                    f"Control Plane MRO stack: {sorted(actual)!r}"
                )
                continue
            seen_allowlisted.add(key)
            if actual != expected:
                violations.append(
                    f"{path.relative_to(ROOT)}:{node.lineno} changed reviewed "
                    f"implementation inheritance from {sorted(expected)!r} "
                    f"to {sorted(actual)!r}"
                )

    missing = sorted(set(_ALLOWED_IMPLEMENTATION_MULTIPLE_INHERITANCE) - seen_allowlisted)
    if missing:
        violations.append(
            "stale Control Plane multiple-inheritance allow-list entries: "
            f"{missing!r}"
        )
    assert violations == []


def test_migrated_compatibility_facades_do_not_own_domain_behavior() -> None:
    """Compatibility classes may install modules but may not reimplement domain behavior."""

    allowed = {
        "portability_api.py": {"__init__", "portability_workflow"},
        "plugin_api.py": {"__init__", "plugin_registry", "plugin_catalog", "attach_plugin_runtime"},
        "organization_audit_api.py": {"__init__", "organization_audit"},
        "approval_decision_composition.py": {"__init__"},
    }
    for filename, allowed_methods in allowed.items():
        facade = _class(CONTROL_PLANE / filename, "ControlPlane")
        methods = {
            node.name
            for node in facade.body
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        }
        assert methods <= allowed_methods, (
            f"{filename} compatibility facade regained domain behavior: "
            f"{sorted(methods - allowed_methods)!r}"
        )


def test_canonical_single_node_portability_composition_has_one_control_plane_base() -> None:
    path = CONTROL_PLANE / "approval_portability_composition.py"
    facade = _class(path, "ControlPlane")
    assert len(facade.bases) == 1

    imports = [node for node in _tree(path).body if isinstance(node, ast.ImportFrom)]
    assert not any(
        node.module == "portability_api"
        and any(alias.name == "ControlPlane" for alias in node.names)
        for node in imports
    )


def test_plugin_terminal_composition_has_one_control_plane_base() -> None:
    path = CONTROL_PLANE / "plugin_terminal_composition.py"
    facade = _class(path, "ControlPlane")
    assert len(facade.bases) == 1

    imports = [node for node in _tree(path).body if isinstance(node, ast.ImportFrom)]
    assert not any(
        node.module == "plugin_api"
        and any(alias.name == "ControlPlane" for alias in node.names)
        for node in imports
    )


def test_migrated_domains_declare_explicit_module_owners() -> None:
    portability = (CONTROL_PLANE / "portability_module.py").read_text(encoding="utf-8")
    plugins = (CONTROL_PLANE / "plugin_module.py").read_text(encoding="utf-8")
    organization_audit = (CONTROL_PLANE / "organization_audit_api.py").read_text(
        encoding="utf-8"
    )
    approval_decisions = (CONTROL_PLANE / "approval_decision_module.py").read_text(
        encoding="utf-8"
    )

    assert 'PORTABILITY_MODULE = "portability"' in portability
    assert 'PLUGIN_MODULE = "plugins"' in plugins
    assert 'ORGANIZATION_AUDIT_MODULE = "organization-audit"' in organization_audit
    assert 'APPROVAL_DECISION_MODULE = "approval-decisions"' in approval_decisions
    assert "ControlPlaneModule(" in portability
    assert "ControlPlaneModule(" in plugins
    assert "ControlPlaneModule(" in organization_audit
    assert "ControlPlaneModule(" in approval_decisions
