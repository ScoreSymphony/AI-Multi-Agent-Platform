from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONTROL_PLANE = ROOT / "src" / "ai_multi_agent_platform" / "control_plane"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class(path: Path, name: str) -> ast.ClassDef:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{path.relative_to(ROOT)} does not define {name}")


def test_control_plane_domain_facades_do_not_use_multiple_inheritance() -> None:
    """A domain must not regain implicit composition through Python MRO diamonds."""

    violations: list[str] = []
    for path in sorted(CONTROL_PLANE.glob("*.py")):
        for node in _tree(path).body:
            if (
                isinstance(node, ast.ClassDef)
                and node.name in {"ControlPlane", "ControlPlaneHTTP"}
                and len(node.bases) > 1
            ):
                violations.append(
                    f"{path.relative_to(ROOT)}:{node.lineno} has {len(node.bases)} bases"
                )
    assert violations == []


def test_migrated_compatibility_facades_do_not_own_domain_commands() -> None:
    """Compatibility classes may install modules but may not reimplement domain behavior."""

    allowed = {
        "portability_api.py": {"__init__", "portability_workflow"},
        "plugin_api.py": {"__init__", "plugin_registry", "plugin_catalog", "attach_plugin_runtime"},
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


def test_migrated_domains_declare_explicit_module_owners() -> None:
    portability = (CONTROL_PLANE / "portability_module.py").read_text(encoding="utf-8")
    plugins = (CONTROL_PLANE / "plugin_module.py").read_text(encoding="utf-8")

    assert 'PORTABILITY_MODULE = "portability"' in portability
    assert 'PLUGIN_MODULE = "plugins"' in plugins
    assert "ControlPlaneModule(" in portability
    assert "ControlPlaneModule(" in plugins
