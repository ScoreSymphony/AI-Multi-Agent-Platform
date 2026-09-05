from ai_multi_agent_platform.capabilities import CapabilityCompatibilityRequest
from ai_multi_agent_platform.templates.capability_assignment_handler import _compatibility


def test_template_compatibility_defaults_match_canonical_contract() -> None:
    canonical = CapabilityCompatibilityRequest(maximum_version="2.0")
    parsed = _compatibility({"maximum_version": "2.0"})

    assert parsed == canonical
    assert parsed.include_minimum is True
    assert parsed.include_maximum is False
