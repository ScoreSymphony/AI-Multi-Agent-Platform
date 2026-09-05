"""Deterministic Registry-backed validation for capability-assignment policy."""

from __future__ import annotations

from ai_multi_agent_platform.capabilities import (
    CapabilityCompatibilityRequest,
    CapabilitySpec,
    CredentialRequirement,
    SafetyClassification,
    SideEffectClassification,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.security import RiskClassification

from .contracts import CapabilityInventory
from .models import CapabilityAssignmentContent, CapabilityAssignmentRule


def validate_capability_rules(
    content: CapabilityAssignmentContent,
    inventory_source: CapabilityInventory,
) -> None:
    """Validate canonical references without depending on provider runtime identity."""

    inventory = inventory_source.inventory_capabilities(include_unavailable=True)
    for rule in content.required + content.allowed:
        _validate_rule(rule, inventory, enforce_privilege=True)
    for rule in content.denied:
        _validate_rule(rule, inventory, enforce_privilege=False)


def assignment_risk(content: CapabilityAssignmentContent) -> RiskClassification:
    if any(
        item.privileged or item.approval_required for item in content.required + content.allowed
    ):
        return RiskClassification.HIGH
    return RiskClassification.ELEVATED


def _validate_rule(
    rule: CapabilityAssignmentRule,
    inventory: tuple[CapabilitySpec, ...],
    *,
    enforce_privilege: bool,
) -> None:
    matches = tuple(
        capability
        for capability in inventory
        if capability.capability_id == rule.capability_id and _matches_rule(capability, rule)
    )
    if not matches:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "capability assignment references unavailable canonical capability "
            f"{rule.capability_id!r}",
        )
    if not enforce_privilege:
        return
    if any(_privileged(capability) for capability in matches) and not rule.privileged:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"capability {rule.capability_id!r} requires explicit privileged assignment metadata",
        )
    if any(capability.required_approvals for capability in matches) and not rule.approval_required:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            f"capability {rule.capability_id!r} requires explicit approval metadata",
        )


def _privileged(capability: CapabilitySpec) -> bool:
    return (
        capability.safety is not SafetyClassification.STANDARD
        or capability.side_effects is SideEffectClassification.DESTRUCTIVE
        or capability.credential_requirement is CredentialRequirement.REQUIRED
    )


def _matches_rule(capability: CapabilitySpec, rule: CapabilityAssignmentRule) -> bool:
    if rule.exact_version is not None:
        return capability.version == rule.exact_version
    if rule.compatibility is None:
        return True
    return _matches_compatibility(capability, rule.compatibility)


def _matches_compatibility(
    capability: CapabilitySpec,
    request: CapabilityCompatibilityRequest,
) -> bool:
    if not set(request.required_features).issubset(capability.features):
        return False
    try:
        version = _numeric_version_key(capability.version)
    except ValueError:
        return False
    if request.minimum_version is not None:
        minimum = _numeric_version_key(request.minimum_version)
        if version < minimum or (version == minimum and not request.include_minimum):
            return False
    if request.maximum_version is not None:
        maximum = _numeric_version_key(request.maximum_version)
        if version > maximum or (version == maximum and not request.include_maximum):
            return False
    return True


def _numeric_version_key(value: str) -> tuple[int, int, int]:
    parts = value.split(".")
    if not 1 <= len(parts) <= 3 or any(not part.isdigit() for part in parts):
        raise ValueError("not a canonical comparable version")
    numbers = [int(part) for part in parts]
    numbers.extend([0] * (3 - len(numbers)))
    return numbers[0], numbers[1], numbers[2]
