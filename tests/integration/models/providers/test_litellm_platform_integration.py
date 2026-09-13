"""LiteLLM platform integration coverage originating in issue #11."""

import litellm_hardening_cases as cases

test_committed_examples_flow_through_platform_configuration = (
    cases.test_committed_examples_flow_through_platform_configuration
)
test_platform_router_preserves_self_hosted_only_policy = (
    cases.test_platform_router_preserves_self_hosted_only_policy
)
