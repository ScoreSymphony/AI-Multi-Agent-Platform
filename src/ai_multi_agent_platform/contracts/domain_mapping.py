"""Explicit mappings from provider-neutral contract DTOs into canonical domain identities."""

from __future__ import annotations

import hashlib
import json

from ai_multi_agent_platform.domain import ExternalRef, OwnerRef, Provenance, validate_id
from ai_multi_agent_platform.domain import ToolInvocation as DomainToolInvocation

from .types import ToolInvocation as ContractToolInvocation


def tool_invocation_arguments_digest(invocation: ContractToolInvocation) -> str:
    """Return the stable SHA-256 digest of the immutable governed arguments."""

    encoded = json.dumps(
        invocation.arguments_json(),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


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
    already-resolved canonical Tool (and optional Project) identity. The returned
    ``tool_invocation_<uuid>`` is the stable subject used by Approval, Event and audit
    records. Its provenance binds that identity to the immutable invocation arguments
    via a deterministic SHA-256 digest.
    """

    validate_id(canonical_tool_id, "tool")
    if canonical_project_id is not None:
        validate_id(canonical_project_id, "project")
    if not provider_namespace.strip():
        raise ValueError("provider_namespace must not be blank")

    arguments_digest = tool_invocation_arguments_digest(invocation)
    return DomainToolInvocation(
        tool_id=canonical_tool_id,
        owner_ref=owner_ref,
        project_id=canonical_project_id,
        correlation_id=invocation.context.correlation_id,
        causation_id=invocation.context.causation_id,
        provenance=Provenance(
            source="tool_invocation_contract_mapping",
            details={"arguments_sha256": arguments_digest},
        ),
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
