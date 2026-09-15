"""Translation of canonical policy profiles into the local authorization provider shape."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import ContractError, ErrorCode

from .authorization import ActorType, LocalPrincipalPolicy
from .policy_profile_models import AuthorizationPolicyProfileRevision


def compile_local_principal_policy(
    revision: AuthorizationPolicyProfileRevision,
    *,
    principal_ref: str,
    actor_types: tuple[ActorType, ...],
) -> LocalPrincipalPolicy:
    """Compile one canonical revision into the local reference provider's private shape.

    Canonical identity remains ``revision.ref``; replacing the local provider does not
    rewrite, re-key or otherwise mutate the profile resource.
    """

    conditions = revision.content.conditions
    if (
        conditions.required_security_labels
        or conditions.allowed_node_ids
        or conditions.allowed_side_effects
    ):
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "LocalAuthorizationProvider cannot represent canonical policy conditions",
            details={"policy_profile_ref": revision.ref.token},
        )
    scope = revision.content.scope_constraints
    if scope.resource_ids:
        raise ContractError(
            ErrorCode.UNSUPPORTED_CAPABILITY,
            "LocalAuthorizationProvider cannot represent resource-ID policy constraints",
            details={"policy_profile_ref": revision.ref.token},
        )
    project_ids = scope.project_ids or ((revision.project_id,) if revision.project_id else ())
    organization_ids = scope.organization_ids or (
        (revision.organization_id,) if revision.organization_id else ()
    )
    team_ids = scope.team_ids or ((revision.team_id,) if revision.team_id else ())
    return LocalPrincipalPolicy(
        principal_ref=principal_ref,
        actor_types=frozenset(actor_types),
        allowed_actions=frozenset(revision.content.allowed_actions),
        approval_actions=frozenset(revision.content.approval_required_actions),
        resource_types=frozenset(revision.content.resource_types),
        project_ids=frozenset(project_ids),
        organization_ids=frozenset(organization_ids),
        team_ids=frozenset(team_ids),
        workspace_ids=frozenset(scope.workspace_ids),
    )
