"""Platform-owned canonical identity binding for capability invocations.

Provider tool references and provider invocation handles are external evidence only. This
module derives the canonical Tool and ToolInvocation identities from platform-owned capability,
Run and invocation inputs so retries do not create a second identity merely because a backend
handle changes.
"""

from __future__ import annotations

import json
from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

from ai_multi_agent_platform.contracts.domain_mapping import (
    map_tool_invocation_to_domain,
    tool_invocation_arguments_digest,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import ToolInvocation
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.domain import ToolInvocation as DomainToolInvocation

from .types import CapabilityInvocation, CapabilityRegistration

_TOOL_NAMESPACE = "ai-multi-agent-platform:canonical-capability-tool"
_TOOL_INVOCATION_NAMESPACE = "ai-multi-agent-platform:canonical-capability-tool-invocation"


def canonical_tool_id(capability_id: str, capability_version: str) -> str:
    """Return the stable canonical Tool identity for one canonical capability version."""

    if not capability_id.strip():
        raise ValueError("capability_id must not be blank")
    if not capability_version.strip():
        raise ValueError("capability_version must not be blank")
    seed = json.dumps(
        [capability_id, capability_version],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"tool_{uuid5(NAMESPACE_URL, f'{_TOOL_NAMESPACE}:{seed}')}"


def canonical_tool_invocation_id(
    request: CapabilityInvocation,
    registration: CapabilityRegistration,
    provider_invocation: ToolInvocation,
) -> str:
    """Return an idempotent ToolInvocation ID independent from provider-private handles.

    The platform invocation key is caller-owned and, for Agent turns, is derived from the
    canonical Run plus tool-call ordinal rather than a model/provider call handle. The immutable
    argument digest is included so changed arguments cannot silently reuse an earlier canonical
    invocation identity.
    """

    capability = registration.capability
    if request.capability_id != capability.capability_id:
        raise ValueError("capability request does not match resolved registration")
    tool_id = canonical_tool_id(capability.capability_id, capability.version)
    arguments_digest = tool_invocation_arguments_digest(provider_invocation)
    seed = json.dumps(
        [
            request.trace.run_id,
            request.invocation_id,
            tool_id,
            arguments_digest,
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return f"tool_invocation_{uuid5(NAMESPACE_URL, f'{_TOOL_INVOCATION_NAMESPACE}:{seed}')}"


async def bind_canonical_capability_invocation(
    request: CapabilityInvocation,
    registration: CapabilityRegistration,
    provider_invocation: ToolInvocation,
) -> DomainToolInvocation:
    """Bind one resolved capability call to a canonical platform ToolInvocation.

    Ownership and Project scope must already come from the canonical runtime context. The binder
    deliberately fails closed rather than fabricating ownership. Provider invocation/tool handles
    remain ExternalRefs through the shared contract/domain mapper and do not participate in the
    canonical Tool identity.
    """

    context = request.context
    if context.owner_type is None or context.owner_id is None:
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "canonical capability invocation binding requires owner context",
            provider_id=registration.provider_id,
        )
    owner_ref = OwnerRef(type=context.owner_type, id=context.owner_id)
    mapped = map_tool_invocation_to_domain(
        provider_invocation,
        canonical_tool_id=canonical_tool_id(
            registration.capability.capability_id,
            registration.capability.version,
        ),
        owner_ref=owner_ref,
        canonical_project_id=context.project_id,
    )
    return replace(
        mapped,
        id=canonical_tool_invocation_id(request, registration, provider_invocation),
    )


__all__ = [
    "bind_canonical_capability_invocation",
    "canonical_tool_id",
    "canonical_tool_invocation_id",
]
