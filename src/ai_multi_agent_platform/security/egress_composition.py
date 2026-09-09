"""Composition helpers for one shared durable egress-policy runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ai_multi_agent_platform.contracts import AuthorizationProvider
from ai_multi_agent_platform.contracts.egress import EgressAuditSink

from .egress import (
    CanonicalEgressPolicy,
    EgressActorResolver,
    EgressGate,
)
from .egress_approvals import EgressApprovalBridge, EgressApprovalExceptionPolicy
from .egress_profiles import EgressProfileService, JsonEgressProfileRepository
from .egress_resolution import RepositoryBackedEgressPolicy
from .enforcement import AuthorizationGate


@dataclass(frozen=True, slots=True)
class DurableEgressRuntime:
    """Long-lived objects shared by provider-facing execution boundaries."""

    repository: JsonEgressProfileRepository
    profiles: EgressProfileService
    gate: EgressGate


def build_durable_egress_runtime(
    path: str | Path,
    *,
    authorization: AuthorizationProvider | None = None,
    approval_gate: AuthorizationGate | None = None,
    approval_policy: EgressApprovalExceptionPolicy | None = None,
    actor_resolver: EgressActorResolver | None = None,
    audit_sink: EgressAuditSink | None = None,
    allow_paid_external: bool = False,
    allow_unknown_external_cost: bool = False,
    require_external_profile: bool = True,
) -> DurableEgressRuntime:
    """Build one reusable policy gate for model/capability/connector/context/file egress.

    Durable production-shaped composition requires an explicit EgressProfile for external targets
    by default. This closes the legacy ambiguity where an external destination with no trust/cost
    profile could otherwise bypass the baseline paid/unknown-external policy. Focused compatibility
    embeddings may opt out explicitly with ``require_external_profile=False``; direct
    ``CanonicalEgressPolicy`` use retains its legacy profileless behavior.

    ``approval_policy`` is deliberately inert unless the existing #15 ``approval_gate`` is also
    supplied. The default exception policy approves no reason code, so ordinary deployments gain
    no new disclosure authority merely by constructing this runtime.
    """

    repository = JsonEgressProfileRepository(path)
    profiles = EgressProfileService(repository, authorization=authorization)
    canonical = CanonicalEgressPolicy(
        allow_paid_external=allow_paid_external,
        allow_unknown_external_cost=allow_unknown_external_cost,
    )
    policy = RepositoryBackedEgressPolicy(
        repository,
        canonical,
        require_external_profile=require_external_profile,
    )

    exception_policy = approval_policy or EgressApprovalExceptionPolicy()
    approval_resolver = (
        None if approval_gate is None else EgressApprovalBridge(approval_gate, exception_policy)
    )
    gate = EgressGate(
        policy,
        audit_sink=audit_sink,
        approval_resolver=approval_resolver,
        actor_resolver=actor_resolver,
    )
    return DurableEgressRuntime(repository=repository, profiles=profiles, gate=gate)


__all__ = ["DurableEgressRuntime", "build_durable_egress_runtime"]
