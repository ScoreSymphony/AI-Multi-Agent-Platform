from __future__ import annotations

import ast
from pathlib import Path


def test_domain_layer_has_no_vendor_framework_imports() -> None:
    domain_dir = Path(__file__).parents[3] / "src" / "ai_multi_agent_platform" / "domain"
    forbidden_roots = {"hermes", "forge", "temporal"}

    for path in domain_dir.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                roots = {alias.name.split(".", 1)[0].lower() for alias in node.names}
                assert roots.isdisjoint(forbidden_roots)
            elif isinstance(node, ast.ImportFrom) and node.module:
                root = node.module.split(".", 1)[0].lower()
                assert root not in forbidden_roots
