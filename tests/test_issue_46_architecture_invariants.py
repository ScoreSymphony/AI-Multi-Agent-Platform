from __future__ import annotations

import re
import tomllib
from pathlib import Path

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
_MANDATORY_DEPENDENCY_DENYLIST = {
    "anthropic",
    "forge",
    "hermes",
    "litellm",
    "mcp",
    "openai",
}


def test_canonical_core_does_not_import_optional_backend_implementations() -> None:
    offenders: list[str] = []
    for root in _CORE_ROOTS:
        assert root.is_dir(), f"missing canonical source root: {root}"
        for path in sorted(root.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            if any(pattern.search(source) for pattern in _OPTIONAL_BACKEND_IMPORTS):
                offenders.append(path.as_posix())

    assert offenders == []


def test_optional_backend_packages_are_not_mandatory_runtime_dependencies() -> None:
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    dependency_names = {
        re.split(r"[<>=!~\[; ]", dependency.lower(), maxsplit=1)[0].replace("_", "-")
        for dependency in dependencies
    }

    assert dependency_names.isdisjoint(_MANDATORY_DEPENDENCY_DENYLIST)
