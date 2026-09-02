from __future__ import annotations

import ast
from pathlib import Path

CONTRACT_DIR = Path("src/ai_multi_agent_platform/contracts")
FORBIDDEN_IMPORT_PREFIXES = (
    "ai_multi_agent_platform.adapters",
    "hermes",
    "forge",
    "litellm",
    "mcp",
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


def test_contract_layer_does_not_import_adapters_or_vendor_frameworks() -> None:
    for path in CONTRACT_DIR.glob("*.py"):
        for module in _imported_modules(path):
            assert not module.startswith(FORBIDDEN_IMPORT_PREFIXES), (
                f"{path} imports forbidden implementation module {module!r}"
            )
