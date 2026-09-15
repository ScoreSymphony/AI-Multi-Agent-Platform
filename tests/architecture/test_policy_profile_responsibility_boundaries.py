"""Architecture guardrails for authorization policy-profile ownership."""

from ai_multi_agent_platform.security import (
    AuthorizationPolicyProfileService as PackageAuthorizationPolicyProfileService,
)
from ai_multi_agent_platform.security.policy_profile_compilation import (
    compile_local_principal_policy as owned_compile_local_principal_policy,
)
from ai_multi_agent_platform.security.policy_profile_models import (
    AuthorizationPolicyProfileDefinition as OwnedAuthorizationPolicyProfileDefinition,
)
from ai_multi_agent_platform.security.policy_profile_repository import (
    InMemoryAuthorizationPolicyProfileRepository as OwnedInMemoryPolicyProfileRepository,
)
from ai_multi_agent_platform.security.policy_profile_service import (
    AuthorizationPolicyProfileService as OwnedAuthorizationPolicyProfileService,
)
from ai_multi_agent_platform.security.policy_profiles import (
    AuthorizationPolicyProfileDefinition,
    AuthorizationPolicyProfileService,
    InMemoryAuthorizationPolicyProfileRepository,
    compile_local_principal_policy,
)


def test_policy_profile_facade_preserves_public_identity_and_ownership() -> None:
    assert AuthorizationPolicyProfileDefinition is OwnedAuthorizationPolicyProfileDefinition
    assert InMemoryAuthorizationPolicyProfileRepository is OwnedInMemoryPolicyProfileRepository
    assert AuthorizationPolicyProfileService is OwnedAuthorizationPolicyProfileService
    assert PackageAuthorizationPolicyProfileService is OwnedAuthorizationPolicyProfileService
    assert compile_local_principal_policy is owned_compile_local_principal_policy
