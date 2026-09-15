from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "ci" / "maintainability_guard.py"


def _load_guard() -> ModuleType:
    spec = importlib.util.spec_from_file_location("maintainability_guard_test_target", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_config(
    path: Path,
    *,
    module_review: int = 5,
    module_extreme: int = 10,
    function_review: int = 3,
    function_extreme: int = 6,
    complexity_review: int = 2,
    complexity_extreme: int = 4,
    exemptions: str = "exemptions = []",
) -> None:
    path.write_text(
        f"""{exemptions}

[thresholds]
module_review_lines = {module_review}
module_extreme_lines = {module_extreme}
function_review_lines = {function_review}
function_extreme_lines = {function_extreme}
function_review_complexity = {complexity_review}
function_extreme_complexity = {complexity_extreme}
""",
        encoding="utf-8",
    )


def _write_module(root: Path, source: str) -> None:
    source_root = root / "src" / "ai_multi_agent_platform"
    source_root.mkdir(parents=True)
    (source_root / "sample.py").write_text(source, encoding="utf-8")


@pytest.mark.unit
def test_inventory_reports_module_function_size_and_branch_complexity(tmp_path: Path) -> None:
    guard = _load_guard()
    _write_module(
        tmp_path,
        """def branchy(value: int) -> int:
    if value > 0 and value < 10:
        return value
    if value == 10:
        return 0
    return -1
""",
    )
    config_path = tmp_path / "maintainability.toml"
    _write_config(config_path)

    inventory = guard.build_inventory(
        tmp_path,
        guard.load_configuration(config_path),
    )

    module = inventory["modules"][0]
    function = module["functions"][0]
    assert module["path"] == "src/ai_multi_agent_platform/sample.py"
    assert module["lines"] == 6
    assert function["qualname"] == "branchy"
    assert function["lines"] == 6
    assert function["complexity"] == 4
    assert function["review"] is True
    assert function["extreme"] is False


@pytest.mark.unit
def test_compare_only_rejects_new_unexempt_extremes(tmp_path: Path) -> None:
    guard = _load_guard()
    config_path = tmp_path / "maintainability.toml"
    _write_config(
        config_path,
        module_review=10,
        module_extreme=20,
        function_review=3,
        function_extreme=6,
        complexity_review=10,
        complexity_extreme=20,
    )
    config = guard.load_configuration(config_path)

    baseline_root = tmp_path / "baseline"
    _write_module(
        baseline_root,
        """def work(value: int) -> int:
    if value:
        return value
    return 0
""",
    )
    current_root = tmp_path / "current"
    _write_module(
        current_root,
        """def work(value: int) -> int:
    if value > 10:
        return 10
    if value > 5:
        return 5
    if value > 0:
        return value
    return 0
""",
    )

    baseline = guard.build_inventory(baseline_root, config)
    current = guard.build_inventory(current_root, config)
    regressions = guard.compare_inventories(baseline, current)

    assert len(regressions) == 1
    assert "function src/ai_multi_agent_platform/sample.py:work" in regressions[0]

    historical = guard.compare_inventories(current, current)
    assert historical == []


@pytest.mark.unit
def test_explicit_function_exemption_requires_reason_and_suppresses_delta(tmp_path: Path) -> None:
    guard = _load_guard()
    source = """def generated_mapping(value: int) -> int:
    if value > 10:
        return 10
    if value > 5:
        return 5
    if value > 0:
        return value
    return 0
"""
    baseline_root = tmp_path / "baseline"
    _write_module(baseline_root, "def generated_mapping(value: int) -> int:\n    return value\n")
    current_root = tmp_path / "current"
    _write_module(current_root, source)

    config_path = tmp_path / "maintainability.toml"
    _write_config(
        config_path,
        function_review=3,
        function_extreme=6,
        complexity_review=10,
        complexity_extreme=20,
        exemptions="""[[exemptions]]
kind = "function"
path = "src/ai_multi_agent_platform/sample.py"
symbol = "generated_mapping"
reason = "Generated mapping fixture is intentionally declarative."
""",
    )
    config = guard.load_configuration(config_path)
    current = guard.build_inventory(current_root, config)

    function = current["modules"][0]["functions"][0]
    assert function["extreme"] is True
    assert function["exemption"] == "Generated mapping fixture is intentionally declarative."
    assert (
        guard.compare_inventories(
            guard.build_inventory(baseline_root, config),
            current,
        )
        == []
    )


@pytest.mark.unit
def test_exemption_without_reason_is_rejected(tmp_path: Path) -> None:
    guard = _load_guard()
    config_path = tmp_path / "maintainability.toml"
    _write_config(
        config_path,
        exemptions="""[[exemptions]]
kind = "module"
path = "src/ai_multi_agent_platform/generated.py"
reason = ""
""",
    )

    with pytest.raises(ValueError, match="reason must be a non-blank string"):
        guard.load_configuration(config_path)
