from __future__ import annotations

import asyncio
import importlib
import pkgutil

from ai_multi_agent_platform import contracts
from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ContractError,
    ErrorCode,
    ExecutionRequest,
    ModelProvider,
    ModelRequest,
    ModelResponse,
    OperationContext,
    PlanRequest,
    ProviderDescriptor,
    ToolInvocation,
)
from ai_multi_agent_platform.testing.conformance import (
    assert_canonical_error,
    assert_model_provider_contract,
)
from ai_multi_agent_platform.testing.fakes import (
    FakeLifecycleBackend,
    FakeModelProvider,
    FakeOrchestrator,
    FakeToolProvider,
)

CTX = OperationContext(correlation_id="validation-task")


class SecondTestModelAdapter(ModelProvider):
    """Independent test adapter proving model replacement at the contract boundary."""

    descriptor = ProviderDescriptor(
        provider_id="second-test-model",
        provider_type="model",
        supported_operations=("generate",),
    )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return ModelResponse(
            request_id=request.request_id,
            text="second-provider",
            model_ref="second-test-model/default",
        )


class BackendSdkError(RuntimeError):
    pass


class TranslatingModelAdapter(ModelProvider):
    """Example adapter that contains a backend-private exception."""

    descriptor = ProviderDescriptor(
        provider_id="translation-test-model",
        provider_type="model",
        supported_operations=("generate",),
    )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        del request
        try:
            raise BackendSdkError("private SDK failure")
        except BackendSdkError as error:
            raise ContractError(
                ErrorCode.TRANSIENT_FAILURE,
                "model backend failed",
                retryable=True,
                provider_id=self.descriptor.provider_id,
                adapter_metadata=(
                    AdapterMetadata(
                        namespace="translation_test",
                        values={"exception_type": type(error).__name__},
                    ),
                ),
            ) from error


def test_canonical_task_flow_runs_using_only_reference_providers() -> None:
    task_id = "task-validation"
    orchestrator = FakeOrchestrator()
    model = FakeModelProvider(response_text="model-output")
    tool = FakeToolProvider(fixed_output={"tool": "output"}, echo_arguments=False)
    lifecycle = FakeLifecycleBackend()

    plan = asyncio.run(
        orchestrator.plan(PlanRequest(task_id=task_id, context=CTX, objective="validate contracts"))
    )
    model_response = asyncio.run(
        model.generate(
            ModelRequest(
                request_id="model-validation",
                messages=(plan.summary,),
                context=CTX,
            )
        )
    )
    tool_result = asyncio.run(
        tool.invoke(
            ToolInvocation(
                invocation_id="tool-validation",
                tool_ref="tool.echo",
                arguments={"model_output": model_response.text},
                context=CTX,
            )
        )
    )
    execution = asyncio.run(
        lifecycle.start(
            ExecutionRequest(
                run_id="run-validation",
                subject_type="task",
                subject_id=task_id,
                context=CTX,
                input={"plan_ref": plan.plan_ref, "tool_output": tool_result.output},
            )
        )
    )

    assert plan.plan_ref == f"plan:{task_id}"
    assert model_response.request_id == "model-validation"
    assert tool_result.invocation_id == "tool-validation"
    assert execution.run_id == "run-validation"
    assert orchestrator.calls[0].task_id == task_id
    assert lifecycle.start_calls[0].subject_id == task_id


def test_model_provider_can_be_replaced_without_changing_canonical_request() -> None:
    request = ModelRequest(
        request_id="same-request",
        messages=("same canonical input",),
        context=CTX,
    )
    first = FakeModelProvider(response_text="first-provider")
    second = SecondTestModelAdapter()

    first_response = asyncio.run(first.generate(request))
    second_response = asyncio.run(second.generate(request))

    assert first_response.request_id == request.request_id
    assert second_response.request_id == request.request_id
    assert first_response.text == "first-provider"
    assert second_response.text == "second-provider"
    asyncio.run(assert_model_provider_contract(SecondTestModelAdapter(), request))


def test_backend_private_exception_is_translated_to_canonical_error() -> None:
    adapter = TranslatingModelAdapter()
    request = ModelRequest(
        request_id="translation-request",
        messages=("trigger",),
        context=CTX,
    )

    error = asyncio.run(
        assert_canonical_error(
            lambda: adapter.generate(request),
            expected_code=ErrorCode.TRANSIENT_FAILURE,
        )
    )

    assert error.retryable is True
    assert error.provider_id == "translation-test-model"
    assert error.adapter_metadata[0].namespace == "translation_test"


def test_core_import_graph_loads_without_optional_adapter_dependencies() -> None:
    root_package = importlib.import_module("ai_multi_agent_platform")
    imported = {root_package.__name__, contracts.__name__}

    for module in pkgutil.walk_packages(
        root_package.__path__,
        prefix=f"{root_package.__name__}.",
    ):
        if ".adapters." in module.name or module.name.endswith(".adapters"):
            continue
        imported.add(importlib.import_module(module.name).__name__)

    assert "ai_multi_agent_platform.contracts" in imported
    assert not any(name.startswith("hermes") for name in imported)
    assert not any(name.startswith("forge") for name in imported)
    assert not any(name.startswith("litellm") for name in imported)
