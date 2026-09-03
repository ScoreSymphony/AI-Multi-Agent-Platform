"""Browser provider binding for placement and redacted operation metadata."""

from __future__ import annotations

from dataclasses import dataclass, replace
from time import perf_counter
from urllib.parse import urlsplit, urlunsplit

from ai_multi_agent_platform.capabilities.types import CapabilityRegistration
from ai_multi_agent_platform.contracts.errors import ContractError
from ai_multi_agent_platform.contracts.types import (
    AdapterMetadata,
    JsonValue,
    OperationContext,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.domain import validate_id

from .contracts import BrowserProvider
from .models import BrowserProviderFeatures, BrowserSessionRef


@dataclass(frozen=True, slots=True)
class BrowserPlacement:
    """Optional placement constraints applied without changing canonical browser requests."""

    node_id: str | None = None
    worker_id: str | None = None
    priority: int | None = None
    required_worker_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.node_id is not None:
            validate_id(self.node_id, "node")
        if self.worker_id is not None:
            validate_id(self.worker_id, "worker")
        if any(not capability.strip() for capability in self.required_worker_capabilities):
            raise ValueError("required_worker_capabilities must not contain blank values")
        if len(set(self.required_worker_capabilities)) != len(
            self.required_worker_capabilities
        ):
            raise ValueError("required_worker_capabilities must not contain duplicates")


class BoundBrowserProvider(BrowserProvider):
    """Decorate any browser provider with canonical placement and operation evidence metadata."""

    def __init__(
        self,
        inner: BrowserProvider,
        *,
        placement: BrowserPlacement | None = None,
        emit_operation_metadata: bool = True,
    ) -> None:
        self._inner = inner
        self._placement = placement or BrowserPlacement()
        self._emit_operation_metadata = emit_operation_metadata
        self._tool_capabilities: dict[str, str] = {}

    @property
    def descriptor(self) -> ProviderDescriptor:
        return self._inner.descriptor

    @property
    def browser_features(self) -> BrowserProviderFeatures:
        return self._inner.browser_features

    async def get_session(
        self,
        session_id: str,
        context: OperationContext,
    ) -> BrowserSessionRef:
        return await self._inner.get_session(session_id, context)

    async def close_session(self, session_id: str, context: OperationContext) -> None:
        await self._inner.close_session(session_id, context)

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        registrations = await self._inner.capability_registrations()
        bound: list[CapabilityRegistration] = []
        for registration in registrations:
            self._tool_capabilities[registration.provider_tool_ref] = (
                registration.capability.capability_id
            )
            capability = registration.capability
            if self._placement.required_worker_capabilities:
                merged = tuple(
                    dict.fromkeys(
                        (
                            *capability.required_worker_capabilities,
                            *self._placement.required_worker_capabilities,
                        )
                    )
                )
                capability = replace(
                    capability,
                    required_worker_capabilities=merged,
                )

            placement_metadata: tuple[AdapterMetadata, ...] = ()
            if (
                self._placement.node_id is not None
                or self._placement.worker_id is not None
                or self._placement.required_worker_capabilities
            ):
                placement_metadata = (
                    AdapterMetadata(
                        namespace="browser.placement",
                        values={
                            "node_id": self._placement.node_id,
                            "worker_id": self._placement.worker_id,
                            "required_worker_capabilities": list(
                                self._placement.required_worker_capabilities
                            ),
                        },
                    ),
                )

            bound.append(
                replace(
                    registration,
                    capability=capability,
                    priority=(
                        registration.priority
                        if self._placement.priority is None
                        else self._placement.priority
                    ),
                    node_id=(
                        registration.node_id
                        if self._placement.node_id is None
                        else self._placement.node_id
                    ),
                    worker_id=(
                        registration.worker_id
                        if self._placement.worker_id is None
                        else self._placement.worker_id
                    ),
                    adapter_metadata=(
                        *registration.adapter_metadata,
                        *placement_metadata,
                    ),
                )
            )
        return tuple(bound)

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        if not self._tool_capabilities:
            await self.capability_registrations()

        started = perf_counter()
        arguments = invocation.arguments_json()
        requested_url = arguments.get("url")
        requested_url = requested_url if isinstance(requested_url, str) else None
        capability_id = self._tool_capabilities.get(invocation.tool_ref, invocation.tool_ref)

        try:
            result = await self._inner.invoke(invocation)
        except ContractError as exc:
            if self._emit_operation_metadata:
                metadata = _operation_metadata(
                    capability_id=capability_id,
                    requested_url=requested_url,
                    final_url=None,
                    duration_ms=(perf_counter() - started) * 1000,
                    outcome="failed",
                    error_code=exc.code.value,
                )
                exc.adapter_metadata = (*exc.adapter_metadata, metadata)
            raise

        if not self._emit_operation_metadata:
            return result

        final_url: str | None = None
        content_trust: JsonValue = None
        if isinstance(result.output, dict):
            output_url = result.output.get("url")
            final_url = output_url if isinstance(output_url, str) else None
            content_trust = result.output.get("content_trust")

        metadata = _operation_metadata(
            capability_id=capability_id,
            requested_url=requested_url,
            final_url=final_url,
            duration_ms=(perf_counter() - started) * 1000,
            outcome="succeeded",
            content_trust=content_trust,
            result_ref=result.result_ref,
        )
        return replace(
            result,
            adapter_metadata=(*result.adapter_metadata, metadata),
        )


def _operation_metadata(
    *,
    capability_id: str,
    requested_url: str | None,
    final_url: str | None,
    duration_ms: float,
    outcome: str,
    error_code: str | None = None,
    content_trust: JsonValue = None,
    result_ref: str | None = None,
) -> AdapterMetadata:
    requested_target, requested_domain = _redacted_target(requested_url)
    final_target, final_domain = _redacted_target(final_url)
    return AdapterMetadata(
        namespace="browser.operation",
        values={
            "capability_id": capability_id,
            "operation": capability_id.removeprefix("browser."),
            "outcome": outcome,
            "duration_ms": round(duration_ms, 3),
            "requested_target": requested_target,
            "requested_domain": requested_domain,
            "final_target": final_target,
            "final_domain": final_domain,
            "content_trust": content_trust,
            "result_ref": result_ref,
            "error_code": error_code,
        },
    )


def _redacted_target(url: str | None) -> tuple[str | None, str | None]:
    """Return URL metadata without credentials, query parameters or fragments."""

    if url is None:
        return None, None
    try:
        parsed = urlsplit(url)
        host = parsed.hostname
        port = parsed.port
    except ValueError:
        return None, None
    if not parsed.scheme or host is None:
        return None, None

    display_host = f"[{host}]" if ":" in host else host
    netloc = display_host if port is None else f"{display_host}:{port}"
    target = urlunsplit((parsed.scheme.lower(), netloc, parsed.path or "/", "", ""))
    return target, host.lower()
