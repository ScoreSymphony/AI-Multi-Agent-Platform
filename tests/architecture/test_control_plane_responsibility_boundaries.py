from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL_PLANE_ROOT = ROOT / "src" / "ai_multi_agent_platform" / "control_plane"
SERVICE = CONTROL_PLANE_ROOT / "service.py"
SCOPE_STORE = CONTROL_PLANE_ROOT / "scope_store.py"
HEALTH = CONTROL_PLANE_ROOT / "health.py"
MODEL_REGISTRY = CONTROL_PLANE_ROOT / "model_registry_service.py"


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


def _assert_delegate(
    method: ast.FunctionDef | ast.AsyncFunctionDef, component: str, call: str
) -> None:
    calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)]
    assert any(
        isinstance(item.func, ast.Attribute)
        and item.func.attr == call
        and isinstance(item.func.value, ast.Attribute)
        and isinstance(item.func.value.value, ast.Name)
        and item.func.value.value.id == "self"
        and item.func.value.attr == component
        for item in calls
    ), f"{method.name} must delegate to self.{component}.{call}"


def _assert_no_facade_dependency(path: Path) -> None:
    violations: list[int] = []
    for node in ast.walk(_tree(path)):
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
        f"{path.relative_to(ROOT)} must not depend back on the ControlPlane façade: "
        + ", ".join(str(line) for line in violations)
    )


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


def test_focused_control_plane_components_do_not_depend_back_on_facade() -> None:
    _assert_no_facade_dependency(SCOPE_STORE)
    _assert_no_facade_dependency(HEALTH)
    _assert_no_facade_dependency(MODEL_REGISTRY)


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


def test_health_aggregation_stays_behind_focused_component() -> None:
    facade = _class(SERVICE, "ControlPlane")
    _assert_delegate(_method(facade, "health"), "_health", "health")
    health = _class(HEALTH, "ControlPlaneHealth")
    _method(health, "health")


def test_model_registry_operations_stay_behind_focused_component() -> None:
    facade = _class(SERVICE, "ControlPlane")
    delegated = {
        "list_model_providers": "list_providers",
        "get_model_provider": "get_provider",
        "set_model_provider_enabled": "set_provider_enabled",
        "refresh_model_provider_health": "refresh_provider_health",
        "list_models": "list_models",
        "get_model": "get_model",
        "set_model_enabled": "set_model_enabled",
    }
    for facade_method, component_method in delegated.items():
        _assert_delegate(_method(facade, facade_method), "_models", component_method)

    component = _class(MODEL_REGISTRY, "ControlPlaneModelRegistry")
    for component_method in (*delegated.values(), "require_registry"):
        _method(component, component_method)
