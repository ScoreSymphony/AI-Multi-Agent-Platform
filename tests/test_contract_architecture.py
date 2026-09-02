from __future__ import annotations

import ast
from pathlib import Path

CORE_ROOT = Path("src/ai_multi_agent_platform")
FORBIDDEN_IMPORT_PREFIXES = (
    "ai_multi_agent_platform.adapters",
    "hermes",
    "forge",
    "litellm",
    "mcp",
    "temporalio",
    "openai",
)


def _imported_modules(path: Path) -> tuple[str, ...]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            modules.append(node.module)

    return tuple(modules)


def _core_python_files() -> tuple[Path, ...]:
    return tuple(
        path
        for path in CORE_ROOT.rglob("*.py")
        if "adapters" not in path.parts
    )


def test_core_does_not_import_concrete_adapters_or_vendor_frameworks() -> None:
    for path in _core_python_files():
        for module in _imported_modules(path):
            assert not module.startswith(FORBIDDEN_IMPORT_PREFIXES), (
                f"{path} imports forbidden implementation module {module!r}"
            )
