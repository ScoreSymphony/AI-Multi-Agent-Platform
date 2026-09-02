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
    already-resolved canonical Tool (and optional Project) identity. A deterministic
    digest of the exact argument payload is retained alongside those references so an
    Approval/Event for the canonical invocation cannot silently authorize later-mutated
    arguments.
    """

    validate_id(canonical_tool_id, "tool")
    if canonical_project_id is not None:
        validate_id(canonical_project_id, "project")
    if not provider_namespace.strip():
        raise ValueError("provider_namespace must not be blank")

    return DomainToolInvocation(
        tool_id=canonical_tool_id,
        owner_ref=owner_ref,
        project_id=canonical_project_id,
        correlation_id=invocation.context.correlation_id,
        causation_id=invocation.context.causation_id,
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
            ExternalRef(
                system="platform",
                kind="arguments_sha256",
                value=tool_arguments_digest(invocation.arguments),
            ),
        ),
    )


def validate_tool_invocation_binding(
    invocation: ContractToolInvocation,
    canonical_invocation: DomainToolInvocation,
) -> None:
    """Reject execution when a contract invocation no longer matches its approved identity."""

    refs = {(ref.kind, ref.value) for ref in canonical_invocation.external_refs}
    if ("invocation_id", invocation.invocation_id) not in refs:
        raise ValueError("tool invocation id does not match canonical governed invocation")
    if ("tool_ref", invocation.tool_ref) not in refs:
        raise ValueError("tool reference does not match canonical governed invocation")

    expected_digest = tool_arguments_digest(invocation.arguments)
    if ("arguments_sha256", expected_digest) not in refs:
        raise ValueError("tool invocation arguments changed after governance binding")
