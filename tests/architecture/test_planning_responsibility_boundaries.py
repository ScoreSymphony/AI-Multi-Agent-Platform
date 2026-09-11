from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLANNING_ROOT = ROOT / "src" / "ai_multi_agent_platform" / "planning"
SUPERSESSION = PLANNING_ROOT / "supersession.py"
COMPOSITION = PLANNING_ROOT / "composition.py"
INVENTORY = PLANNING_ROOT / "inventory.py"
VALIDATION = PLANNING_ROOT / "validation.py"
REPLANNING = PLANNING_ROOT / "replanning.py"
PROPOSALS = PLANNING_ROOT / "proposals.py"
HANDOFF = PLANNING_ROOT / "handoff.py"


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class(path: Path, name: str) -> ast.ClassDef:
    for node in _tree(path).body:
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    raise AssertionError(f"{path.relative_to(ROOT)} does not define {name}")


def _method(class_node: ast.ClassDef, name: str) -> ast.AsyncFunctionDef | ast.FunctionDef:
    for node in class_node.body:
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{class_node.name} does not define {name}")


def _assert_no_service_dependency(path: Path) -> None:
    violations: list[str] = []
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and node.module in {
            "service",
            "supersession",
            "ai_multi_agent_platform.planning.service",
            "ai_multi_agent_platform.planning.supersession",
        }:
            violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
        elif isinstance(node, ast.Import) and any(
            alias.name
            in {
                "ai_multi_agent_platform.planning.service",
                "ai_multi_agent_platform.planning.supersession",
            }
            for alias in node.names
        ):
            violations.append(f"{path.relative_to(ROOT)}:{node.lineno}")
    assert not violations, (
        "focused planning components must not depend back on planning service façades:\n"
        + "\n".join(violations)
    )


def _assert_delegated_call(
    service: ast.ClassDef,
    method_name: str,
    delegated_call: str,
) -> None:
    method = _method(service, method_name)
    calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)]
    assert any(
        isinstance(call.func, ast.Attribute) and call.func.attr == delegated_call
        for call in calls
    ), f"PlanningService.{method_name} must delegate to {delegated_call}"


def test_public_planning_service_delegates_base_inventory_construction() -> None:
    service = _class(SUPERSESSION, "PlanningService")
    method = _method(service, "_inventory")
    calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)]
    assert any(
        isinstance(call.func, ast.Name) and call.func.id == "PlanningInventoryBuilder"
        for call in calls
    ), "PlanningService._inventory must construct the focused inventory builder"
    assert any(
        isinstance(call.func, ast.Attribute) and call.func.attr == "build" for call in calls
    ), "PlanningService._inventory must delegate canonical inventory construction to build()"


def test_public_planning_service_delegates_proposal_validation() -> None:
    service = _class(SUPERSESSION, "PlanningService")
    method = _method(service, "validate")
    calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)]
    assert any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "validate"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "_PROPOSAL_VALIDATOR"
        for call in calls
    ), "PlanningService.validate must delegate to PlanningProposalValidator"


def test_public_planning_service_delegates_replan_support() -> None:
    service = _class(SUPERSESSION, "PlanningService")
    expected = {
        "_prior_plan": "prior_plan",
        "_enforce_replan_budget": "enforce_budget",
        "_trigger_fingerprint": "trigger_fingerprint",
    }
    for method_name, delegated_call in expected.items():
        _assert_delegated_call(service, method_name, delegated_call)


def test_public_planning_service_delegates_immutable_proposal_construction() -> None:
    service = _class(SUPERSESSION, "PlanningService")
    method = _method(service, "_proposal")
    calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)]
    assert any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "build"
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "_PROPOSAL_FACTORY"
        for call in calls
    ), "PlanningService._proposal must delegate base proposal construction to PlanningProposalFactory"


def test_public_planning_service_delegates_canonical_activation_handoff() -> None:
    service = _class(SUPERSESSION, "PlanningService")
    expected = {
        "_activated_plan_event": "activated_plan_event",
        "_plan_ref": "plan_ref",
        "_failed_replan_can_activate": "failed_replan_can_activate",
        "_ensure_handoff_ready": "ensure_ready",
        "_handoff_to_coordinator": "handoff",
    }
    for method_name, delegated_call in expected.items():
        _assert_delegated_call(service, method_name, delegated_call)


def test_reference_inventory_filtering_remains_layered_on_public_inventory_seam() -> None:
    service = _class(COMPOSITION, "ReferencePlanningService")
    method = _method(service, "_inventory")
    calls = [node for node in ast.walk(method) if isinstance(node, ast.Call)]
    assert any(
        isinstance(call.func, ast.Attribute)
        and call.func.attr == "_inventory"
        and isinstance(call.func.value, ast.Call)
        and isinstance(call.func.value.func, ast.Name)
        and call.func.value.func.id == "super"
        for call in calls
    ), "ReferencePlanningService must retain trusted filtering on top of the base inventory seam"


def test_inventory_component_owns_scope_filtered_canonical_projection() -> None:
    builder = _class(INVENTORY, "PlanningInventoryBuilder")
    _method(builder, "build")
    _method(builder, "scope_compatible")
    _assert_no_service_dependency(INVENTORY)


def test_validator_component_owns_deterministic_proposal_checks() -> None:
    validator = _class(VALIDATION, "PlanningProposalValidator")
    for method_name in (
        "validate",
        "has_model_requirements",
        "model_matches",
        "has_cycle",
        "peak_parallelism",
        "contains_provider_private_metadata",
        "capability_map",
    ):
        _method(validator, method_name)
    _assert_no_service_dependency(VALIDATION)


def test_replan_component_owns_prior_plan_trigger_and_budget_policy() -> None:
    support = _class(REPLANNING, "PlanningReplanSupport")
    _method(support, "prior_plan")
    _method(support, "enforce_budget")
    _method(support, "trigger_fingerprint")
    _assert_no_service_dependency(REPLANNING)


def test_proposal_factory_owns_immutable_base_proposal_construction() -> None:
    factory = _class(PROPOSALS, "PlanningProposalFactory")
    _method(factory, "build")
    _assert_no_service_dependency(PROPOSALS)


def test_handoff_component_owns_canonical_plan_reconstruction_and_registration() -> None:
    handoff = _class(HANDOFF, "PlanningActivationHandoff")
    for method_name in (
        "activated_plan_event",
        "ensure_ready",
        "handoff",
        "plan_ref",
        "failed_replan_can_activate",
    ):
        _method(handoff, method_name)
    _assert_no_service_dependency(HANDOFF)
