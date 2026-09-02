"""Canonical capability invocation pipeline."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from typing import Protocol

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue, ToolInvocation

from .registry import CapabilityRegistry
from .types import (
    CapabilityInvocation,
    CapabilityInvocationResult,
    CapabilityRegistration,
    CapabilitySpec,
    InvocationRecord,
    InvocationStatus,
    PolicyDecision,
)

type PolicyHook = Callable[[CapabilityInvocation, CapabilitySpec], Awaitable[PolicyDecision]]
type ApprovalHook = Callable[[CapabilityInvocation, CapabilitySpec], Awaitable[bool]]


class InvocationObserver(Protocol):
    async def record(self, record: InvocationRecord) -> None: ...


class NullInvocationObserver:
    async def record(self, record: InvocationRecord) -> None:
        return None


class CapabilityInvoker:
    """Resolve, validate, authorize, invoke and normalize one capability request."""

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        policy_hook: PolicyHook | None = None,
        approval_hook: ApprovalHook | None = None,
        observer: InvocationObserver | None = None,
    ) -> None:
        self._registry = registry
        self._policy_hook = policy_hook
        self._approval_hook = approval_hook
        self._observer = observer or NullInvocationObserver()

    async def invoke(self, request: CapabilityInvocation) -> CapabilityInvocationResult:
        registration, provider = self._registry.resolve(
            request.capability_id,
            version=request.version,
            granted_permissions=request.granted_permissions,
            available_worker_capabilities=request.available_worker_capabilities,
        )
        capability = registration.capability
        self._validate_schema(
            capability.input_schema,
            dict(request.arguments),
            stage="input",
            capability_id=capability.capability_id,
        )

        if self._policy_hook is not None:
            decision = await self._policy_hook(request, capability)
            if decision is PolicyDecision.DENY:
                await self._record(
                    request, registration, InvocationStatus.DENIED, ErrorCode.FORBIDDEN.value
                )
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    f"capability {capability.capability_id!r} denied by policy hook",
                    provider_id=registration.provider_id,
                )
            if decision is PolicyDecision.REQUIRE_APPROVAL:
                await self._require_approval(request, capability, registration)

        missing_approvals = set(capability.required_approvals) - set(request.approval_grants)
        if missing_approvals:
            await self._require_approval(request, capability, registration)

        provider_invocation = ToolInvocation(
            invocation_id=request.invocation_id,
            tool_ref=registration.provider_tool_ref,
            arguments=request.arguments,
            context=request.context,
        )

        await self._record(request, registration, InvocationStatus.RUNNING)
        timeout = capability.timeout_seconds or request.context.control.timeout_seconds

        try:
            if timeout is None:
                tool_result = await provider.invoke(provider_invocation)
            else:
                tool_result = await asyncio.wait_for(provider.invoke(provider_invocation), timeout)
        except TimeoutError as exc:
            await self._record(
                request, registration, InvocationStatus.TIMED_OUT, ErrorCode.TIMEOUT.value
            )
            raise ContractError(
                ErrorCode.TIMEOUT,
                f"capability {capability.capability_id!r} timed out",
                provider_id=registration.provider_id,
                retryable=True,
            ) from exc
        except asyncio.CancelledError as exc:
            await self._record(
                request, registration, InvocationStatus.CANCELLED, ErrorCode.CANCELLED.value
            )
            raise ContractError(
                ErrorCode.CANCELLED,
                f"capability {capability.capability_id!r} was cancelled",
                provider_id=registration.provider_id,
                retryable=True,
            ) from exc
        except ContractError as exc:
            await self._record(request, registration, InvocationStatus.FAILED, exc.code.value)
            raise
        except Exception as exc:
            await self._record(
                request, registration, InvocationStatus.FAILED, ErrorCode.BACKEND_ERROR.value
            )
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                f"provider failed while invoking {capability.capability_id!r}",
                provider_id=registration.provider_id,
            ) from exc

        if tool_result.invocation_id != request.invocation_id:
            await self._record(
                request,
                registration,
                InvocationStatus.FAILED,
                ErrorCode.CONTRACT_VIOLATION.value,
            )
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "tool provider returned a mismatched invocation_id",
                provider_id=registration.provider_id,
            )

        if capability.output_schema is not None:
            self._validate_schema(
                capability.output_schema,
                tool_result.output,
                stage="output",
                capability_id=capability.capability_id,
            )

        result = CapabilityInvocationResult(
            invocation_id=request.invocation_id,
            capability_id=capability.capability_id,
            capability_version=capability.version,
            provider_id=registration.provider_id,
            status=InvocationStatus.SUCCEEDED,
            output=tool_result.output,
            adapter_metadata=tool_result.adapter_metadata,
        )
        await self._record(request, registration, InvocationStatus.SUCCEEDED)
        return result

    async def _require_approval(
        self,
        request: CapabilityInvocation,
        capability: CapabilitySpec,
        registration: CapabilityRegistration,
    ) -> None:
        approved = False
        if self._approval_hook is not None:
            approved = await self._approval_hook(request, capability)
        if not approved:
            await self._record(
                request,
                registration,
                InvocationStatus.APPROVAL_REQUIRED,
                ErrorCode.FORBIDDEN.value,
            )
            raise ContractError(
                ErrorCode.FORBIDDEN,
                f"capability {capability.capability_id!r} requires approval",
                details={"approval_required": True},
            )

    async def _record(
        self,
        request: CapabilityInvocation,
        registration: CapabilityRegistration,
        status: InvocationStatus,
        error_code: str | None = None,
    ) -> None:
        await self._observer.record(
            InvocationRecord(
                invocation_id=request.invocation_id,
                capability_id=registration.capability.capability_id,
                capability_version=registration.capability.version,
                provider_id=registration.provider_id,
                provider_tool_ref=registration.provider_tool_ref,
                status=status,
                trace=request.trace,
                error_code=error_code,
                adapter_metadata=registration.adapter_metadata,
            )
        )

    @staticmethod
    def _validate_schema(
        schema: Mapping[str, JsonValue],
        value: JsonValue,
        *,
        stage: str,
        capability_id: str,
    ) -> None:
        schema_dict = dict(schema)
        try:
            Draft202012Validator.check_schema(schema_dict)
            Draft202012Validator(schema_dict).validate(value)
        except (SchemaError, ValidationError) as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST
                if stage == "input"
                else ErrorCode.CONTRACT_VIOLATION,
                f"{stage} schema validation failed for capability {capability_id!r}: {exc.message}",
            ) from exc
