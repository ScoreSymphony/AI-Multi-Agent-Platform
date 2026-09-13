from __future__ import annotations

import ast
import re
import tomllib
from collections.abc import Iterable
from pathlib import Path

from ai_multi_agent_platform.domain import ExternalRef, OwnerRef, Run, Task, new_id, validate_id

_CORE_ROOTS = (
    Path("src/ai_multi_agent_platform/contracts"),
    Path("src/ai_multi_agent_platform/domain"),
    Path("src/ai_multi_agent_platform/kernel"),
)
_OPTIONAL_BACKEND_IMPORTS = (
    re.compile(
        r"^\s*(?:from|import)\s+ai_multi_agent_platform\.adapters(?:\.|\s|$)",
        re.MULTILINE,
    ),
    re.compile(
        r"^\s*(?:from|import)\s+(?:hermes|forge|litellm|mcp)(?:\.|\s|$)",
        re.MULTILINE,
    ),
)
_BACKEND_PRIVATE_TYPE_PREFIXES = ("hermes", "forge", "litellm", "mcp")
_MANDATORY_DEPENDENCY_DENYLIST = {
    "anthropic",
    "forge",
    "hermes",
    "litellm",
    "mcp",
    "openai",
}


def _canonical_python_files() -> Iterable[Path]:
    for root in _CORE_ROOTS:
        assert root.is_dir(), f"missing canonical source root: {root}"
        yield from sorted(root.rglob("*.py"))


def _public_type_nodes(tree: ast.AST) -> Iterable[ast.AST]:
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            yield node.annotation
        elif isinstance(node, ast.arg) and node.annotation is not None:
            yield node.annotation
        elif isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) and node.returns is not None:
            yield node.returns
        elif isinstance(node, ast.ClassDef):
            yield from node.bases


def _expanded_type_nodes(type_node: ast.AST) -> Iterable[ast.AST]:
    pending = [type_node]
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            try:
                parsed = ast.parse(node.value, mode="eval")
            except SyntaxError:
                continue
            pending.append(parsed.body)
            continue
        pending.extend(ast.iter_child_nodes(node))


def _backend_private_type_names(tree: ast.AST) -> set[str]:
    offenders: set[str] = set()
    for type_node in _public_type_nodes(tree):
        for node in _expanded_type_nodes(type_node):
            name: str | None = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            if name is not None and name.lower().startswith(_BACKEND_PRIVATE_TYPE_PREFIXES):
                offenders.add(name)
    return offenders


def test_backend_private_type_guard_inspects_quoted_forward_references() -> None:
    tree = ast.parse(
        """
class LeakyContract:
    direct: "HermesResponse"
    nested: list["ForgeExecution"]

    def invoke(self, request: "LiteLLMRequest") -> "MCPResult": ...
"""
    )

    assert _backend_private_type_names(tree) == {
        "ForgeExecution",
        "HermesResponse",
        "LiteLLMRequest",
        "MCPResult",
    }


def test_canonical_core_does_not_import_optional_backend_implementations() -> None:
    offenders: list[str] = []
    for path in _canonical_python_files():
        source = path.read_text(encoding="utf-8")
        if any(pattern.search(source) for pattern in _OPTIONAL_BACKEND_IMPORTS):
            offenders.append(path.as_posix())

    assert offenders == []


def test_canonical_core_public_types_do_not_reference_backend_private_classes() -> None:
    offenders: dict[str, list[str]] = {}
    for path in _canonical_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=path.as_posix())
        names = sorted(_backend_private_type_names(tree))
        if names:
            offenders[path.as_posix()] = names

    assert offenders == {}


def test_canonical_shaped_backend_ids_remain_namespaced_external_refs() -> None:
    owner = OwnerRef(type="user", id="issue-46")
    external_task_id = new_id("task")
    external_run_id = new_id("run")
    validate_id(external_task_id, "task")
    validate_id(external_run_id, "run")

    hermes_task = ExternalRef(system="hermes", kind="task", value=external_task_id)
    forge_run = ExternalRef(system="forge", kind="run", value=external_run_id)
    task = Task(title="Canonical identity", owner_ref=owner, external_refs=(hermes_task,))
    run = Run(
        subject_type="task",
        subject_id=task.id,
        owner_ref=owner,
        correlation_id=task.id,
        external_refs=(forge_run,),
    )

    validate_id(task.id, "task")
    validate_id(run.id, "run")
    assert task.external_refs == (hermes_task,)
    assert run.external_refs == (forge_run,)
    assert task.id != external_task_id
    assert run.id != external_run_id


def test_optional_backend_packages_are_not_mandatory_runtime_dependencies() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    dependency_names = {
        re.split(r"[<>=!~\[; ]", dependency.lower(), maxsplit=1)[0].replace("_", "-")
        for dependency in dependencies
    }

    assert dependency_names.isdisjoint(_MANDATORY_DEPENDENCY_DENYLIST)
