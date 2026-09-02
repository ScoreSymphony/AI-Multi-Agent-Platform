import pytest

from ai_multi_agent_platform.contracts import (
    OperationContext,
    map_tool_invocation_to_domain,
    tool_invocation_arguments_digest,
    validate_tool_invocation_binding,
)
from ai_multi_agent_platform.contracts import (
    ToolInvocation as ContractToolInvocation,
)
from ai_multi_agent_platform.domain import OwnerRef, Tool
from ai_multi_agent_platform.domain import ToolInvocation as DomainToolInvocation

OWNER = OwnerRef(type="user", id="user-1")
PROVIDER = "fake_tool_provider"


def _context(
    *,
    correlation_id: str = "corr-binding",
    causation_id: str | None = "event-before-binding",
    owner_id: str | None = None,
) -> OperationContext:
    return OperationContext(
        correlation_id=correlation_id,
        causation_id=causation_id,
        owner_type="user" if owner_id is not None else None,
        owner_id=owner_id,
    )


def _approved_invocation() -> tuple[ContractToolInvocation, DomainToolInvocation]:
    tool = Tool(name="filesystem-write", owner_ref=OWNER)
    invocation = ContractToolInvocation(
        invocation_id="provider-invoke-1",
        tool_ref="provider-tool-write",
        arguments={"path": "approved.txt", "options": {"overwrite": False}},
        context=_context(),
    )
    canonical = map_tool_invocation_to_domain(
        invocation,
        canonical_tool_id=tool.id,
        owner_ref=OWNER,
        provider_namespace=PROVIDER,
    )
    return invocation, canonical


def test_governed_tool_invocation_binding_accepts_exact_mapped_call() -> None:
    invocation, canonical = _approved_invocation()

    validate_tool_invocation_binding(
        invocation,
        canonical,
        provider_namespace=PROVIDER,
    )

    assert canonical.provenance is not None
    assert canonical.provenance.details["arguments_sha256"] == tool_invocation_arguments_digest(
        invocation
    )


def test_governed_tool_invocation_rejects_reconstructed_different_arguments() -> None:
    approved, canonical = _approved_invocation()
    changed = ContractToolInvocation(
        invocation_id=approved.invocation_id,
        tool_ref=approved.tool_ref,
        arguments={"path": "different.txt", "options": {"overwrite": False}},
        context=approved.context,
    )

    with pytest.raises(ValueError, match="arguments changed"):
        validate_tool_invocation_binding(
            changed,
            canonical,
            provider_namespace=PROVIDER,
        )


def test_governed_tool_invocation_rejects_replaced_provider_handle() -> None:
    approved, canonical = _approved_invocation()
    replaced = ContractToolInvocation(
        invocation_id="provider-invoke-replaced",
        tool_ref=approved.tool_ref,
        arguments=approved.arguments_json(),
        context=approved.context,
    )

    with pytest.raises(ValueError, match="provider handle changed"):
        validate_tool_invocation_binding(
            replaced,
            canonical,
            provider_namespace=PROVIDER,
        )


def test_governed_tool_invocation_rejects_changed_correlation_context() -> None:
    approved, canonical = _approved_invocation()
    changed = ContractToolInvocation(
        invocation_id=approved.invocation_id,
        tool_ref=approved.tool_ref,
        arguments=approved.arguments_json(),
        context=_context(correlation_id="corr-other"),
    )

    with pytest.raises(ValueError, match="correlation context changed"):
        validate_tool_invocation_binding(
            changed,
            canonical,
            provider_namespace=PROVIDER,
        )


def test_governed_tool_invocation_rejects_conflicting_declared_owner() -> None:
    approved, canonical = _approved_invocation()
    changed = ContractToolInvocation(
        invocation_id=approved.invocation_id,
        tool_ref=approved.tool_ref,
        arguments=approved.arguments_json(),
        context=_context(owner_id="user-2"),
    )

    with pytest.raises(ValueError, match="ownership context changed"):
        validate_tool_invocation_binding(
            changed,
            canonical,
            provider_namespace=PROVIDER,
        )


def test_governed_tool_invocation_rejects_unmapped_canonical_identity() -> None:
    tool = Tool(name="filesystem-write", owner_ref=OWNER)
    invocation = ContractToolInvocation(
        invocation_id="provider-invoke-unmapped",
        tool_ref="provider-tool-write",
        arguments={"path": "approved.txt"},
        context=_context(),
    )
    canonical = DomainToolInvocation(
        tool_id=tool.id,
        owner_ref=OWNER,
        correlation_id=invocation.context.correlation_id,
        causation_id=invocation.context.causation_id,
    )

    with pytest.raises(ValueError, match="provider handle changed|no governed argument binding"):
        validate_tool_invocation_binding(
            invocation,
            canonical,
            provider_namespace=PROVIDER,
        )
