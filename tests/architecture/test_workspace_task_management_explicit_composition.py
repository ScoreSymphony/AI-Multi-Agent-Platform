from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL_PLANE = ROOT / "src" / "ai_multi_agent_platform" / "control_plane"


def _tree(filename: str) -> ast.Module:
    path = CONTROL_PLANE / filename
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class(filename: str, name: str) -> ast.ClassDef:
    for node in _tree(filename).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{filename} does not define {name}")


def test_historical_workspace_task_management_path_is_behavior_free() -> None:
    tree = _tree("workspace_task_management_api.py")
    assert not any(isinstance(node, ast.ClassDef) for node in tree.body)
    imports = [node for node in tree.body if isinstance(node, ast.ImportFrom)]
    assert any(
        node.module == "workspace_task_management_registered_composition"
        and any(alias.name == "ControlPlane" for alias in node.names)
        for node in imports
    )


def test_registered_workspace_task_management_composition_has_one_control_plane_base() -> None:
    facade = _class("workspace_task_management_registered_composition.py", "ControlPlane")
    assert [ast.unparse(base) for base in facade.bases] == ["_LinearControlPlane"]


def test_linear_workspace_task_management_composition_has_no_task_management_parent() -> None:
    facade = _class("workspace_task_management_explicit_composition.py", "ControlPlane")
    bases = {ast.unparse(base) for base in facade.bases}
    assert bases == {
        "RepositoryRunProvenanceMixin",
        "AuthorizationBoundaryHardeningMixin",
        "_RunWorkspaceControlPlane",
    }
    assert "_TaskManagementControlPlane" not in bases
