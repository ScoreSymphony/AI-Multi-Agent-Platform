from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
KERNEL_ROOT = ROOT / "src" / "ai_multi_agent_platform" / "kernel"
KERNEL_FACADE = KERNEL_ROOT / "kernel.py"
DATA_ROOT = ROOT / "src" / "ai_multi_agent_platform" / "data"
DATA_FACADE = DATA_ROOT / "reference.py"


def _class(path: Path, name: str) -> ast.ClassDef:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{path.relative_to(ROOT)} does not define {name}")


def _method(class_node: ast.ClassDef, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in class_node.body:
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{class_node.name} does not define {name}")


def _delegated_attribute(method: ast.AsyncFunctionDef | ast.FunctionDef) -> str | None:
    """Return ``self.<attribute>`` for a one-statement awaited delegation."""

    if len(method.body) != 1 or not isinstance(method.body[0], ast.Return):
        return None
    expression = method.body[0].value
    if isinstance(expression, ast.Await):
        expression = expression.value
    if not isinstance(expression, ast.Call):
        return None
    function = expression.func
    if not isinstance(function, ast.Attribute) or not isinstance(function.value, ast.Attribute):
        return None
    receiver = function.value
    if not isinstance(receiver.value, ast.Name) or receiver.value.id != "self":
        return None
    return receiver.attr


def test_kernel_read_queries_stay_behind_focused_query_component() -> None:
    facade = _class(KERNEL_FACADE, "PlatformKernel")
    for method_name in ("get_task", "get_run", "history"):
        method = _method(facade, method_name)
        assert _delegated_attribute(method) == "_queries", (
            f"PlatformKernel.{method_name} reabsorbed query mechanics; keep canonical reads "
            "behind KernelQueries or deliberately update the #723 responsibility map"
        )


def test_kernel_recovery_stays_behind_focused_recovery_component() -> None:
    facade = _class(KERNEL_FACADE, "PlatformKernel")
    for method_name in ("recover_task", "recover_all"):
        method = _method(facade, method_name)
        assert _delegated_attribute(method) == "_recovery", (
            f"PlatformKernel.{method_name} reabsorbed recovery mechanics; keep restart/reconcile "
            "coordination behind KernelRecovery or deliberately update the #723 responsibility map"
        )


def test_extracted_kernel_components_do_not_depend_on_concrete_facade() -> None:
    violations: list[str] = []
    for filename in ("queries.py", "recovery.py"):
        path = KERNEL_ROOT / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            imported = {alias.name for alias in node.names}
            if node.module in {"kernel", "ai_multi_agent_platform.kernel.kernel"} and (
                "PlatformKernel" in imported
            ):
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not violations, (
        "focused kernel components must depend on narrow contracts/protocols, not the concrete "
        "PlatformKernel façade:\n" + "\n".join(violations)
    )


def test_data_reference_facade_does_not_reabsorb_provider_implementations() -> None:
    tree = ast.parse(DATA_FACADE.read_text(encoding="utf-8"), filename=str(DATA_FACADE))
    classes = [node.name for node in tree.body if isinstance(node, ast.ClassDef)]
    assert not classes, (
        "data/reference.py is a compatibility façade after #723; provider implementations must "
        f"remain in responsibility-focused modules, found classes: {classes}"
    )


def test_data_reference_providers_have_focused_implementation_modules() -> None:
    expected = {
        "reference_file.py": "LocalFileProvider",
        "reference_memory.py": "LocalMemoryProvider",
        "reference_knowledge.py": "LocalKnowledgeProvider",
    }
    for filename, class_name in expected.items():
        _class(DATA_ROOT / filename, class_name)


def test_focused_data_provider_modules_do_not_depend_on_reference_facade() -> None:
    violations: list[str] = []
    for filename in ("reference_file.py", "reference_memory.py", "reference_knowledge.py"):
        path = DATA_ROOT / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom):
                continue
            if node.module in {"reference", "ai_multi_agent_platform.data.reference"}:
                violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not violations, (
        "focused data provider modules must not depend on the compatibility façade:\n"
        + "\n".join(violations)
    )
