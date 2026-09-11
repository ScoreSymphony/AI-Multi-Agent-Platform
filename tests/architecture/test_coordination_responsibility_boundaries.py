from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COORDINATION_ROOT = ROOT / "src" / "ai_multi_agent_platform" / "coordination"
SERVICE = COORDINATION_ROOT / "service.py"
REGISTRATION = COORDINATION_ROOT / "registration.py"
PROGRESSION = COORDINATION_ROOT / "progression.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class(path: Path, name: str) -> ast.ClassDef:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{path.relative_to(ROOT)} does not define {name}")


def _method(class_node: ast.ClassDef, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in class_node.body:
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{class_node.name} does not define {name}")


def _assert_no_facade_dependency(path: Path) -> None:
    violations: list[str] = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "service",
            "ai_multi_agent_platform.coordination.service",
        }:
            violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        elif isinstance(node, ast.Import) and any(
            alias.name == "ai_multi_agent_platform.coordination.service" for alias in node.names
        ):
            violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not violations, (
        "focused coordination components must depend on narrow contracts, not the coordinator "
        "façade:\n" + "\n".join(violations)
    )


def _assert_delegate(method: ast.AsyncFunctionDef | ast.FunctionDef, component: str, call: str) -> None:
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


def test_plan_registration_stays_behind_focused_component() -> None:
    coordinator = _class(SERVICE, "DurablePlanStepCoordinator")
    _assert_delegate(_method(coordinator, "register_plan"), "_registration", "register")


def test_validate_graph_compatibility_shim_delegates_to_registration_component() -> None:
    coordinator = _class(SERVICE, "DurablePlanStepCoordinator")
    method = _method(coordinator, "_validate_graph")
    calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)]
    assert any(
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "CoordinationRegistration"
        and call.func.attr == "validate_graph"
        for call in calls
    ), "_validate_graph must remain a thin compatibility shim over CoordinationRegistration"


def test_progression_compatibility_shims_delegate_to_progression_component() -> None:
    coordinator = _class(SERVICE, "DurablePlanStepCoordinator")
    _assert_delegate(
        _method(coordinator, "_refresh_dependencies"),
        "_progression",
        "refresh_dependencies",
    )
    _assert_delegate(_method(coordinator, "_start_attempt"), "_progression", "start_attempt")


def test_focused_coordination_components_do_not_depend_back_on_facade() -> None:
    _assert_no_facade_dependency(REGISTRATION)
    _assert_no_facade_dependency(PROGRESSION)


def test_registration_component_owns_graph_validation() -> None:
    registration = _class(REGISTRATION, "CoordinationRegistration")
    _method(registration, "register")
    _method(registration, "validate_graph")


def test_progression_component_owns_dependency_and_attempt_mechanics() -> None:
    progression = _class(PROGRESSION, "CoordinationProgression")
    _method(progression, "refresh_dependencies")
    _method(progression, "start_attempt")
