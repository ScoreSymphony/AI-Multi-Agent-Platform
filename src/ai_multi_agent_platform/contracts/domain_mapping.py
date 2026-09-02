"""Explicit mappings from provider-neutral contract DTOs into canonical domain identities."""

from __future__ import annotations

import hashlib
import json

from ai_multi_agent_platform.domain import ExternalRef, OwnerRef, validate_id
from ai_multi_agent_platform.domain import ToolInvocation as DomainToolInvocation

from .types import JsonValue
from .types import ToolInvocation as ContractToolInvocation


def tool_arguments_digest(arguments: dict[str, JsonValue]) -> str:
    """Return a deterministic digest for one tool invocation argument payload."""

    try:
        serialized = json.dumps(
            arguments,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("tool invocation arguments must be canonical JSON values") from exc
    return f"sha256:{hashlib.sha256(serialized.encode('utf-8')).hexdigest()}"


def _validate_security_context(
    invocation: ContractToolInvocation,
    *,
    owner_ref: OwnerRef,
    canonical_project_id: str | None,
) -> None:
    context = invocation.context
    if context.owner_type != owner_ref.type or context.owner_id != owner_ref.id:
        raise ValueError("tool invocation ownership context does not match canonical owner")
    if context.project_id != canonical_project_id:
        raise ValueError("tool invocation project context does not match canonical project")


def map_tool_invocation_to_domain(
    invocation: ContractToolInvocation,
    *,
    canonical_tool_id: str,
    owner_ref: OwnerRef,
    canonical_project_id: str | None = None,
    provider_namespace: str = "tool_provider",
) -> DomainToolInvocation:
    """Create the canonical governed-call identity for one provider invocation.

    Contract-level ``invocation_id`` and ``tool_ref`` values may be backend/provider
    handles, so they are retained only as external references. The caller supplies the
    already-resolved canonical Tool (and optional Project) identity. The exact immutable
    argument snapshot is represented by a deterministic digest on the canonical Tool
    Invocation so an Approval/Event cannot authorize a different later payload.
    """

    validate_id(canonical_tool_id, "tool")
    if canonical_project_id is not None:
        validate_id(canonical_project_id, "project")
    if not provider_namespace.strip():
        raise ValueError("provider_namespace must not be blank")
    _validate_security_context(
        invocation,
        owner_ref=owner_ref,
        canonical_project_id=canonical_project_id,
    )

    return DomainToolInvocation(
        tool_id=canonical_tool_id,
        owner_ref=owner_ref,
        project_id=canonical_project_id,
        correlation_id=invocation.context.correlation_id,
        causation_id=invocation.context.causation_id,
        arguments_digest=tool_arguments_digest(invocation.arguments),
        external_refs=(
            ExternalRef(
                system=provider_namespace,
                kind="invocation_id",
                value=invocation.invocation_id,
            ),
            ExternalRef(
                system=provider_namespace,
                kind="tool_ref",
                value=invocation.tool_ref,
            ),
        ),
    )


def validate_tool_invocation_binding(
    invocation: ContractToolInvocation,
    canonical_invocation: DomainToolInvocation,
    *,
    provider_namespace: str,
) -> None:
    """Reject execution when a provider call differs from its governed identity."""

    if not provider_namespace.strip():
        raise ValueError("provider_namespace must not be blank")
    _validate_security_context(
        invocation,
        owner_ref=canonical_invocation.owner_ref,
        canonical_project_id=canonical_invocation.project_id,
    )
    if invocation.context.correlation_id != canonical_invocation.correlation_id:
        raise ValueError("tool invocation correlation context changed after governance binding")
    if invocation.context.causation_id != canonical_invocation.causation_id:
        raise ValueError("tool invocation causation context changed after governance binding")

    refs = {(ref.system, ref.kind, ref.value) for ref in canonical_invocation.external_refs}
    if (provider_namespace, "invocation_id", invocation.invocation_id) not in refs:
        raise ValueError("tool invocation id/provider does not match canonical governed invocation")
    if (provider_namespace, "tool_ref", invocation.tool_ref) not in refs:
        raise ValueError("tool reference/provider does not match canonical governed invocation")

    expected_digest = tool_arguments_digest(invocation.arguments)
    if canonical_invocation.arguments_digest != expected_digest:
        raise ValueError("tool invocation arguments changed after governance binding")
