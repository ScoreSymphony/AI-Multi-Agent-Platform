"""LiteLLM adapter unit coverage."""

import asyncio

import litellm_adapter_cases as cases
import pytest

test_descriptor_never_exposes_secret_value = cases.test_descriptor_never_exposes_secret_value
test_disabled_adapter_is_unavailable_without_loading_litellm = (
    cases.test_disabled_adapter_is_unavailable_without_loading_litellm
)
test_library_adapter_generation_rejects_missing_credentials = (
    cases.test_library_adapter_generation_rejects_missing_credentials
)
test_library_adapter_health_reports_missing_credentials = (
    cases.test_library_adapter_health_reports_missing_credentials
)
test_library_adapter_maps_common_litellm_error_categories = (
    cases.test_library_adapter_maps_common_litellm_error_categories
)
test_library_adapter_maps_litellm_errors_to_canonical_categories = (
    cases.test_library_adapter_maps_litellm_errors_to_canonical_categories
)
test_library_adapter_maps_timeout_to_canonical_error = (
    cases.test_library_adapter_maps_timeout_to_canonical_error
)
test_library_adapter_translates_canonical_request_and_response = (
    cases.test_library_adapter_translates_canonical_request_and_response
)
test_library_mode_fails_clearly_when_optional_dependency_is_absent = (
    cases.test_library_mode_fails_clearly_when_optional_dependency_is_absent
)
test_proxy_mode_uses_existing_openai_compatible_path_for_local_gateway = (
    cases.test_proxy_mode_uses_existing_openai_compatible_path_for_local_gateway
)


def test_library_adapter_propagates_task_cancellation() -> None:
    async def scenario() -> None:
        started = asyncio.Event()

        async def completion(**kwargs: object) -> object:
            del kwargs
            started.set()
            await asyncio.Event().wait()
            return {}

        provider = cases.make_library_provider(completion=completion)
        request = cases.ModelRequest(
            request_id="req-litellm-cancelled",
            messages=("hello",),
            context=cases.CTX,
            requirements={"model_config_id": "model-local-coder"},
        )
        task = asyncio.create_task(provider.generate(request))
        await started.wait()
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
