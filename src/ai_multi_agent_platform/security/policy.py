"""Minimal secure-default decision helpers used before issue #15 lands."""

from __future__ import annotations

from .types import SecurityContext, SecurityDecision


def baseline_decision(
    context: SecurityContext,
    *,
    explicitly_allowed: bool,
    approval_required: bool = False,
    approval_granted: bool = False,
) -> SecurityDecision:
    """Return a deterministic deny-by-default decision.

    Adapter/private metadata is deliberately not consulted. Authority must come
    from canonical policy/approval inputs, never backend identity or model text.
    ``context`` is accepted so callers keep actor/action/resource binding at the
    enforcement point and so the API can evolve without changing that ownership.
    """

    _ = context
    if not explicitly_allowed:
        return SecurityDecision.DENY
    if approval_required and not approval_granted:
        return SecurityDecision.REQUIRE_APPROVAL
    return SecurityDecision.ALLOW
