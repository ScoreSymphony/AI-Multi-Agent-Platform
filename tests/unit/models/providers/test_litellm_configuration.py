"""LiteLLM configuration/provider unit coverage originating in issue #11."""

import litellm_hardening_cases as cases

test_disabled_library_provider_does_not_load_optional_dependency = (
    cases.test_disabled_library_provider_does_not_load_optional_dependency
)
test_enabled_library_provider_fails_before_registry_attachment_without_dependency = (
    cases.test_enabled_library_provider_fails_before_registry_attachment_without_dependency
)
test_from_mapping_rejects_unknown_configuration = (
    cases.test_from_mapping_rejects_unknown_configuration
)
test_library_retry_configuration_is_forwarded_explicitly = (
    cases.test_library_retry_configuration_is_forwarded_explicitly
)
test_telemetry_disabled_suppresses_litellm_request_metadata = (
    cases.test_telemetry_disabled_suppresses_litellm_request_metadata
)
