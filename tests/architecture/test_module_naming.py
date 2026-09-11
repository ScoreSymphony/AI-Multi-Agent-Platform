from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_ROOT = ROOT / "src" / "ai_multi_agent_platform"

RENAMED_MODULES = (
    ("planning", "planning_facade", "PlanningService"),
    ("coordination", "plan_step_coordinator", "DurablePlanStepCoordinator"),
    ("learning", "governed_learning_workflow", "LearningService"),
    ("onboarding", "first_run_service", "OnboardingService"),
    ("verification", "verification_authority", "VerificationService"),
)


@pytest.mark.parametrize(("package", "canonical_module", "symbol"), RENAMED_MODULES)
def test_legacy_service_imports_resolve_to_semantic_implementation(
    package: str,
    canonical_module: str,
    symbol: str,
) -> None:
    legacy = importlib.import_module(f"ai_multi_agent_platform.{package}.service")
    canonical = importlib.import_module(f"ai_multi_agent_platform.{package}.{canonical_module}")

    legacy_symbol = getattr(legacy, symbol)
    canonical_symbol = getattr(canonical, symbol)
    assert legacy_symbol is canonical_symbol
    assert canonical_symbol.__module__ == (f"ai_multi_agent_platform.{package}.{canonical_module}")


@pytest.mark.parametrize(("package", "canonical_module", "symbol"), RENAMED_MODULES)
def test_legacy_service_modules_are_reexport_only(
    package: str,
    canonical_module: str,
    symbol: str,
) -> None:
    del canonical_module, symbol
    path = PACKAGE_ROOT / package / "service.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    implementation_nodes = (
        ast.ClassDef,
        ast.FunctionDef,
        ast.AsyncFunctionDef,
    )
    assert not any(isinstance(node, implementation_nodes) for node in ast.walk(tree))


@pytest.mark.parametrize(("package", "canonical_module", "symbol"), RENAMED_MODULES)
def test_package_exports_do_not_route_through_legacy_service_shim(
    package: str,
    canonical_module: str,
    symbol: str,
) -> None:
    del symbol
    init_source = (PACKAGE_ROOT / package / "__init__.py").read_text(encoding="utf-8")
    assert "from .service import" not in init_source
    assert f"from .{canonical_module} import" in init_source


def test_module_naming_policy_covers_generic_names_and_related_boundaries() -> None:
    policy = (ROOT / "docs" / "PYTHON_MODULE_NAMING.md").read_text(encoding="utf-8")
    for required in (
        "service.py",
        "models.py",
        "control_plane.py",
        "#721",
        "#723",
        "#726",
        "compatibility shim",
    ):
        assert required in policy
