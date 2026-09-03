from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.browser import (
    BROWSER_NAVIGATE_CAPABILITY_ID,
    BoundBrowserProvider,
    BrowserOperation,
    BrowserPlacement,
    BrowserProvider,
    BrowserProviderFeatures,
    BrowserSessionRef,
)
from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistration,
    CapabilityRegistry,
    CapabilitySpec,
    InvocationRecord,
    InvocationStatus,
    InvocationTrace,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import (
    AdapterMetadata,
    Capability,
    CapabilityKind,
    HealthStatus,
    OperationContext,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.domain import new_id


class _RecordingObserver:
    def __init__(self) -> None:
        self.records: list[InvocationRecord] = []

    async def record(self, record: InvocationRecord) -> None:
        self.records.append(record)


class _FakeBrowserProvider(BrowserProvider):
    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="browser.fake",
            provider_type="browser",
            supported_operations=("navigate",),
            capabilities=(
                Capability(
                    name="browser.web",
                    kind=CapabilityKind.TOOL,
                    supported_operations=("navigate",),
                ),
            ),
            health=HealthStatus.HEALTHY,
        )

    @property
    def browser_features(self) -> BrowserProviderFeatures:
        return BrowserProviderFeatures(
            operations=(BrowserOperation.NAVIGATE,),
            headless=True,
            interactive=False,
            javascript=False,
            file_upload=False,
            file_download=False,
            screenshots=False,
            session_persistence=False,
            proxy_policy=True,
        )

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        return (
            CapabilityRegistration(
                capability=CapabilitySpec(
                    capability_id=BROWSER_NAVIGATE_CAPABILITY_ID,
                    name="Navigate",
                    input_schema={
                        "type": "object",
                        "properties": {"url": {"type": "string"}},
                        "required": ["url"],
                        "additionalProperties": False,
                    },
                    output_schema={"type": "object"},
                    health=HealthStatus.HEALTHY,
                ),
                provider_id=self.descriptor.provider_id,
                provider_tool_ref="fake.navigate",
                priority=5,
            ),
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        arguments = invocation.arguments_json()
        url = arguments.get("url")
        if isinstance(url, str) and "/fail" in url:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "fixture browser failed",
                provider_id=self.descriptor.provider_id,
            )
        return ToolResult(
            invocation_id=invocation.invocation_id,
            output={
                "url": "https://example.test/final?session=secret#fragment",
                "content_trust": "untrusted_web_content",
            },
        )

    async def get_session(
        self,
        session_id: str,
        context: OperationContext,
    ) -> BrowserSessionRef:
        del session_id, context
        raise ContractError(ErrorCode.NOT_FOUND, "fixture has no sessions")

    async def close_session(self, session_id: str, context: OperationContext) -> None:
        del session_id, context


def _request(url: str, *, worker_capability: bool = True) -> CapabilityInvocation:
    project_id = new_id("project")
    context = OperationContext(
        correlation_id="browser-binding",
        owner_type="user",
        owner_id="user-1",
        project_id=project_id,
    )
    return CapabilityInvocation(
        invocation_id="browser-binding-invoke",
        capability_id=BROWSER_NAVIGATE_CAPABILITY_ID,
        arguments={"url": url},
        context=context,
        trace=InvocationTrace(
            correlation_id=context.correlation_id,
            task_id=new_id("task"),
            run_id=new_id("run"),
            agent_id=new_id("agent"),
            project_id=project_id,
        ),
        available_worker_capabilities=(
            frozenset({"browser-runtime"}) if worker_capability else frozenset()
        ),
    )


def _metadata(items: tuple[AdapterMetadata, ...], namespace: str) -> AdapterMetadata:
    return next(item for item in items if item.namespace == namespace)


def test_browser_binding_adds_worker_placement_without_changing_task_request() -> None:
    async def scenario() -> None:
        node_id = new_id("node")
        worker_id = new_id("worker")
        provider = BoundBrowserProvider(
            _FakeBrowserProvider(),
            placement=BrowserPlacement(
                node_id=node_id,
                worker_id=worker_id,
                priority=77,
                required_worker_capabilities=("browser-runtime",),
            ),
        )
        registry = CapabilityRegistry()
        await registry.register_provider(provider)

        with pytest.raises(ContractError) as unavailable:
            registry.resolve(BROWSER_NAVIGATE_CAPABILITY_ID)
        assert unavailable.value.code is ErrorCode.UNAVAILABLE

        registration, resolved = registry.resolve(
            BROWSER_NAVIGATE_CAPABILITY_ID,
            available_worker_capabilities=frozenset({"browser-runtime"}),
        )
        assert resolved is provider
        assert registration.node_id == node_id
        assert registration.worker_id == worker_id
        assert registration.priority == 77
        assert registration.capability.required_worker_capabilities == ("browser-runtime",)
        placement = _metadata(registration.adapter_metadata, "browser.placement")
        assert placement.values["node_id"] == node_id
        assert placement.values["worker_id"] == worker_id

    asyncio.run(scenario())


def test_browser_operation_metadata_is_redacted_and_enters_invocation_record() -> None:
    async def scenario() -> None:
        node_id = new_id("node")
        worker_id = new_id("worker")
        provider = BoundBrowserProvider(
            _FakeBrowserProvider(),
            placement=BrowserPlacement(
                node_id=node_id,
                worker_id=worker_id,
                required_worker_capabilities=("browser-runtime",),
            ),
        )
        registry = CapabilityRegistry()
        await registry.register_provider(provider)
        observer = _RecordingObserver()

        result = await CapabilityInvoker(registry, observer=observer).invoke(
            _request("https://user:password@example.test/path?token=secret#fragment")
        )

        result_metadata = _metadata(result.adapter_metadata, "browser.operation")
        assert result_metadata.values["capability_id"] == BROWSER_NAVIGATE_CAPABILITY_ID
        assert result_metadata.values["operation"] == "navigate"
        assert result_metadata.values["outcome"] == "succeeded"
        assert result_metadata.values["requested_target"] == "https://example.test/path"
        assert result_metadata.values["requested_domain"] == "example.test"
        assert result_metadata.values["final_target"] == "https://example.test/final"
        assert result_metadata.values["final_domain"] == "example.test"
        assert result_metadata.values["content_trust"] == "untrusted_web_content"
        assert isinstance(result_metadata.values["duration_ms"], float)
        assert "password" not in repr(result_metadata.values)
        assert "secret" not in repr(result_metadata.values)

        succeeded = next(
            record for record in observer.records if record.status is InvocationStatus.SUCCEEDED
        )
        recorded_metadata = _metadata(succeeded.adapter_metadata, "browser.operation")
        assert recorded_metadata == result_metadata
        assert succeeded.node_id == node_id
        assert succeeded.worker_id == worker_id
        _metadata(succeeded.adapter_metadata, "browser.placement")

    asyncio.run(scenario())


def test_browser_failure_metadata_preserves_canonical_error_and_trace() -> None:
    async def scenario() -> None:
        provider = BoundBrowserProvider(
            _FakeBrowserProvider(),
            placement=BrowserPlacement(required_worker_capabilities=("browser-runtime",)),
        )
        registry = CapabilityRegistry()
        await registry.register_provider(provider)
        observer = _RecordingObserver()
        request = _request("https://example.test/fail?token=secret")

        with pytest.raises(ContractError) as caught:
            await CapabilityInvoker(registry, observer=observer).invoke(request)
        assert caught.value.code is ErrorCode.UNAVAILABLE

        failed = next(
            record for record in observer.records if record.status is InvocationStatus.FAILED
        )
        assert failed.error_code == ErrorCode.UNAVAILABLE.value
        assert failed.trace.task_id == request.trace.task_id
        assert failed.trace.run_id == request.trace.run_id
        assert failed.trace.agent_id == request.trace.agent_id
        metadata = _metadata(failed.adapter_metadata, "browser.operation")
        assert metadata.values["outcome"] == "failed"
        assert metadata.values["error_code"] == ErrorCode.UNAVAILABLE.value
        assert metadata.values["requested_target"] == "https://example.test/fail"
        assert "secret" not in repr(metadata.values)

    asyncio.run(scenario())
