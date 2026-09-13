"""OpenAI-compatible provider unit coverage originating in issue #10."""

import openai_provider_cases as cases

test_local_provider_lists_models_without_paid_credentials = (
    cases.test_local_provider_lists_models_without_paid_credentials
)
test_missing_secret_reference_fails_as_configuration_error = (
    cases.test_missing_secret_reference_fails_as_configuration_error
)
test_provider_maps_http_failures_to_canonical_errors = (
    cases.test_provider_maps_http_failures_to_canonical_errors
)
test_provider_maps_timeout_without_leaking_transport_exception = (
    cases.test_provider_maps_timeout_without_leaking_transport_exception
)
test_provider_rejects_invalid_provider_response_canonically = (
    cases.test_provider_rejects_invalid_provider_response_canonically
)
test_provider_requires_canonical_target_when_multiple_models_are_configured = (
    cases.test_provider_requires_canonical_target_when_multiple_models_are_configured
)
