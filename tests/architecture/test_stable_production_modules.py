from __future__ import annotations

import ast
import importlib
import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "src" / "ai_multi_agent_platform"
COMPATIBILITY_MODULES = {
    SOURCE_ROOT / "cli" / "issue_214.py",
}
ISSUE_FILENAME = re.compile(r"^issue_\d+\.py$")
ISSUE_IMPORT = re.compile(r"(?:^|\.)issue_\d+(?:\.|:|$)")


def test_production_modules_use_stable_structural_names() -> None:
    issue_numbered = {
        path for path in SOURCE_ROOT.rglob("*.py") if ISSUE_FILENAME.fullmatch(path.name)
    }
    assert issue_numbered == COMPATIBILITY_MODULES


def test_canonical_production_imports_do_not_reference_issue_modules() -> None:
    violations: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        if path in COMPATIBILITY_MODULES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                targets = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                targets = [node.module or ""]
            else:
                continue
            for target in targets:
                if ISSUE_IMPORT.search(target):
                    violations.append(f"{path.relative_to(ROOT)}:{node.lineno}: {target}")
    assert not violations, "issue-numbered production imports:\n" + "\n".join(violations)


def test_packaged_entrypoints_do_not_reference_issue_modules() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        configuration = tomllib.load(handle)
    scripts = configuration["project"]["scripts"]
    violations = {name: target for name, target in scripts.items() if ISSUE_IMPORT.search(target)}
    assert not violations
    assert scripts["platform"] == "ai_multi_agent_platform.cli.app:main"


def test_platform_entrypoint_imports_from_stable_module() -> None:
    with (ROOT / "pyproject.toml").open("rb") as handle:
        target = tomllib.load(handle)["project"]["scripts"]["platform"]
    module_name, attribute = target.split(":", maxsplit=1)
    module = importlib.import_module(module_name)
    assert callable(getattr(module, attribute))


def test_legacy_auth_module_is_only_a_compatibility_alias() -> None:
    auth = importlib.import_module("ai_multi_agent_platform.cli.auth")
    compatibility = importlib.import_module("ai_multi_agent_platform.cli.issue_214")
    assert compatibility.main is auth.main
    assert compatibility.run_cli is auth.run_cli
