import pytest

from ai_multi_agent_platform.capabilities import CapabilityInvoker
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode


def test_input_schema_validation_does_not_expose_rejected_value() -> None:
    private_value = "credential-like-private-value"

    with pytest.raises(ContractError) as caught:
        CapabilityInvoker._validate_schema(
            {"type": "string", "pattern": "^allowed$"},
            private_value,
            stage="input",
            capability_id="tool.redaction-test",
        )

    assert caught.value.code is ErrorCode.INVALID_REQUEST
    assert (
        str(caught.value) == "input schema validation failed for capability 'tool.redaction-test'"
    )
    assert private_value not in str(caught.value)


def test_output_schema_validation_does_not_expose_provider_payload() -> None:
    private_value = "provider-private-output"

    with pytest.raises(ContractError) as caught:
        CapabilityInvoker._validate_schema(
            {"type": "string", "pattern": "^allowed$"},
            private_value,
            stage="output",
            capability_id="tool.redaction-test",
        )

    assert caught.value.code is ErrorCode.CONTRACT_VIOLATION
    assert (
        str(caught.value) == "output schema validation failed for capability 'tool.redaction-test'"
    )
    assert private_value not in str(caught.value)
