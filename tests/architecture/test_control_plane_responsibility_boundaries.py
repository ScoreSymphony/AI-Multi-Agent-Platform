from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL_PLANE_ROOT = ROOT / "src" / "ai_multi_agent_platform" / "control_plane"
SERVICE = CONTROL_PLANE_ROOT / "service.py"
SCOPE_STORE = CONTROL_PLANE_ROOT / "scope_store.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class(path: Path, name: str) -> ast.ClassDef:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{path.relative_to(ROOT)} does not define {name}")


def _method(class_node: ast.ClassDef, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    for node in class_node.body:
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{class_node.name} does not define {name}")


def test_scope_store_implementation_lives_outside_control_plane_facade() -> None:
    service_classes = {
        node.name for node in _tree(SERVICE).body if isinstance(node, ast.ClassDef)
    }
    assert "ScopeStore" not in service_classes
    scope_store = _class(SCOPE_STORE, "ScopeStore")
    for method_name in (
        "create_project",
        "store_project_snapshot",
        "compensate_project",
        "get_project",
        "list_projects",
        "create_workspace",
        "get_workspace",
        "list_workspaces",
    ):
        _method(scope_store, method_name)


def test_control_plane_imports_scope_store_from_focused_module() -> None:
    imports = [node for node in _tree(SERVICE).body if isinstance(node, ast.ImportFrom)]
    assert any(
        node.module == "scope_store"
        and any(alias.name == "ScopeStore" for alias in node.names)
        for node in imports
    ), "ControlPlane must retain ScopeStore through the focused scope_store module"


def test_scope_store_does_not_depend_back_on_control_plane_facade() -> None:
    violations: list[int] = []
    for node in ast.walk(_tree(SCOPE_STORE)):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "service",
            "ai_multi_agent_platform.control_plane.service",
        }:
            violations.append(node.lineno)
        elif isinstance(node, ast.Import) and any(
            alias.name == "ai_multi_agent_platform.control_plane.service" for alias in node.names
        ):
            violations.append(node.lineno)
    assert not violations, (
        "scope identity storage must not depend back on the ControlPlane façade: "
        + ", ".join(str(line) for line in violations)
    )


def test_control_plane_keeps_scope_store_as_injected_stable_boundary() -> None:
    facade = _class(SERVICE, "ControlPlane")
    init = _method(facade, "__init__")
    assignments = [node for node in ast.walk(init) if isinstance(node, ast.Assign)]
    assert any(
        any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "self"
            and target.attr == "_scopes"
            for target in assignment.targets
        )
        for assignment in assignments
    )
    _method(facade, "scopes")
