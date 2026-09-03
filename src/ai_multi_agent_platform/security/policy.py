"""Compatibility helper for simple secure-default decisions.

Issue #15's canonical policy engine is ``LocalAuthorizationProvider`` plus
``AuthorizationGate``. This helper remains for older narrow call sites that only need
an explicit allow/approval boolean without a provider round-trip.
"""

from __future__ import annotations

from .types import SecurityContext, SecurityDecision


def baseline_decision(
    context: SecurityContext,
    *,
    explicitly_allowed: bool,
    approval_required: bool = False,
    approval_granted: bool = False,
) -> SecurityDecision:
    """Return a deterministic deny-by-default compatibility decision."""

    _ = context
    if not explicitly_allowed:
        return SecurityDecision.DENY
    if approval_required and not approval_granted:
        return SecurityDecision.REQUIRE_APPROVAL
    return SecurityDecision.ALLOW
