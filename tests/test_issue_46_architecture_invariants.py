from __future__ import annotations

import ast
import re
import tomllib
from collections.abc import Iterable
from pathlib import Path

import pytest

from ai_multi_agent_platform.domain import ExternalRef, OwnerRef, Run, Task

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


def _backend_private_type_names(tree: ast.AST) -> set[str]:
    offenders: set[str] = set()
    for type_node in _public_type_nodes(tree):
        for node in ast.walk(type_node):
            name: str | None = None
            if isinstance(node, ast.Name):
                name = node.id
            elif isinstance(node, ast.Attribute):
                name = node.attr
            if name is not None and name.lower().startswith(_BACKEND_PRIVATE_TYPE_PREFIXES):
                offenders.add(name)
    return offenders


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


def test_backend_native_ids_remain_external_refs_not_canonical_task_run_identity() -> None:
    owner = OwnerRef(type="user", id="issue-46")
    hermes_run = ExternalRef(system="hermes", kind="run", value="run_hermes_native")
    forge_execution = ExternalRef(system="forge", kind="execution", value="forge_exec_native")
    task = Task(title="Canonical identity", owner_ref=owner, external_refs=(hermes_run,))
    run = Run(
        subject_type="task",
        subject_id=task.id,
        owner_ref=owner,
        correlation_id=task.id,
        external_refs=(forge_execution,),
    )

    assert task.id.startswith("task_")
    assert run.id.startswith("run_")
    assert task.external_refs == (hermes_run,)
    assert run.external_refs == (forge_execution,)
    assert task.id != hermes_run.value
    assert run.id != forge_execution.value

    with pytest.raises(ValueError, match="expected canonical task id"):
        Task(
            id=hermes_run.value,
            title="Invalid external identity",
            owner_ref=owner,
        )
    with pytest.raises(ValueError, match="expected canonical run id"):
        Run(
            id=forge_execution.value,
            subject_type="task",
            subject_id=task.id,
            owner_ref=owner,
            correlation_id=task.id,
        )


def test_optional_backend_packages_are_not_mandatory_runtime_dependencies() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    dependency_names = {
        re.split(r"[<>=!~\[; ]", dependency.lower(), maxsplit=1)[0].replace("_", "-")
        for dependency in dependencies
    }

    assert dependency_names.isdisjoint(_MANDATORY_DEPENDENCY_DENYLIST)
