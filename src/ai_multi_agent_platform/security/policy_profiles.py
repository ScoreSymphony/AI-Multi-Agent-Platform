"""Stable public facade for canonical durable authorization policy profiles.

Implementation ownership is split across focused model, repository, lifecycle-service,
and provider-compilation modules. Existing imports from ``security.policy_profiles`` remain
stable.
"""

from .policy_profile_compilation import compile_local_principal_policy
from .policy_profile_models import (
    POLICY_PROFILE_SCHEMA_VERSION,
    AuthorizationPolicyAssignment,
    AuthorizationPolicyConditions,
    AuthorizationPolicyProfileCallContext,
    AuthorizationPolicyProfileContent,
    AuthorizationPolicyProfileDefinition,
    AuthorizationPolicyProfileRef,
    AuthorizationPolicyProfileRevision,
    AuthorizationPolicyProvenance,
    AuthorizationPolicyScopeConstraints,
)
from .policy_profile_repository import (
    AuthorizationPolicyProfileRepository,
    InMemoryAuthorizationPolicyProfileRepository,
)
from .policy_profile_service import AuthorizationPolicyProfileService

__all__ = [
    "POLICY_PROFILE_SCHEMA_VERSION",
    "AuthorizationPolicyAssignment",
    "AuthorizationPolicyConditions",
    "AuthorizationPolicyProfileCallContext",
    "AuthorizationPolicyProfileContent",
    "AuthorizationPolicyProfileDefinition",
    "AuthorizationPolicyProfileRef",
    "AuthorizationPolicyProfileRepository",
    "AuthorizationPolicyProfileRevision",
    "AuthorizationPolicyProfileService",
    "AuthorizationPolicyProvenance",
    "AuthorizationPolicyScopeConstraints",
    "InMemoryAuthorizationPolicyProfileRepository",
    "compile_local_principal_policy",
]
