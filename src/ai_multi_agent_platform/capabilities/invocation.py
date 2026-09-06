"""Canonical capability invocation pipeline."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from time import perf_counter
from typing import Protocol, runtime_checkable

from jsonschema import Draft202012Validator  # type: ignore[import-untyped]
from jsonschema.exceptions import SchemaError, ValidationError  # type: ignore[import-untyped]

from ai_multi_agent_platform.contracts.domain_mapping import validate_tool_invocation_binding
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import AdapterMetadata, JsonValue, ToolInvocation
from ai_multi_agent_platform.domain import ToolInvocation as DomainToolInvocation

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
type CanonicalInvocationBindingHook = Callable[
    [CapabilityInvocation, CapabilityRegistration, ToolInvocation],
    Awaitable[DomainToolInvocation],
]
type GovernanceBindingHook = Callable[
    [CapabilityInvocation, CapabilityRegistration, ToolInvocation],
    Awaitable[DomainToolInvocation],
]
type ApprovalHook = Callable[
    [CapabilityInvocation, CapabilitySpec, DomainToolInvocation], Awaitable[bool]
]


class InvocationObserver(Protocol):
    async def record(self, record: InvocationRecord) -> None: ...


@runtime_checkable
class InvocationFailureMetadataProvider(Protocol):
    """Optional provider seam for metadata created when the invoker owns failure timing."""

    def invocation_failure_metadata(
        self,
        invocation: ToolInvocation,
        *,
        error_code: str,
        duration_ms: float,
    ) -> tuple[AdapterMetadata, ...]: ...


class NullInvocationObserver:
    async def record(self, record: InvocationRecord) -> None:
        return None


class CapabilityInvoker:
    """Resolve, validate, authorize, govern, invoke and normalize one request.

    ``canonical_binding_hook`` is the ordinary platform identity seam. When configured, it is
    applied to every resolved invocation before policy/approval and provider execution so a
    successful, denied or failed call can retain the same canonical ``tool_invocation_*`` subject.

    ``governance_binding_hook`` is retained as the backwards-compatible approval-only fallback
    for callers that have not yet adopted ordinary canonical binding.
    """

    def __init__(
        self,
        registry: CapabilityRegistry,
        *,
        policy_hook: PolicyHook | None = None,
        canonical_binding_hook: CanonicalInvocationBindingHook | None = None,
        governance_binding_hook: GovernanceBindingHook | None = None,
        approval_hook: ApprovalHook | None = None,
        observer: InvocationObserver | None = None,
    ) -> None:
        self._registry = registry
        self._policy_hook = policy_hook
        self._canonical_binding_hook = canonical_binding_hook
        self._governance_binding_hook = governance_binding_hook
        self._approval_hook = approval_hook
        self._observer = observer or NullInvocationObserver()

    async def invoke(self, request: CapabilityInvocation) -> CapabilityInvocationResult:
        registration, provider = self._registry.resolve(
            request.capability_id,
            version=request.version,
            compatibility=request.compatibility,
            granted_permissions=request.granted_permissions,
            available_worker_capabilities=request.available_worker_capabilities,
        )
        capability = registration.capability
        try:
            self._validate_schema(
                capability.input_schema,
                dict(request.arguments),
                stage="input",
                capability_id=capability.capability_id,
            )
        except ContractError as exc:
            await self._record(request, registration, InvocationStatus.FAILED, exc.code.value)
            raise

        provider_invocation = ToolInvocation(
            invocation_id=request.invocation_id,
            tool_ref=registration.provider_tool_ref,
            arguments=request.arguments,
            context=request.context,
        )

        canonical_invocation: DomainToolInvocation | None = None
        if self._canonical_binding_hook is not None:
            canonical_invocation = await self._bind_invocation(
                request,
                registration,
                provider_invocation,
                self._canonical_binding_hook,
                binding_name="canonical",
            )

        policy_decision = PolicyDecision.ALLOW
        if self._policy_hook is not None:
            policy_decision = await self._policy_hook(request, capability)
        if policy_decision is PolicyDecision.DENY:
            await self._record(
                request,
                registration,
                InvocationStatus.DENIED,
                ErrorCode.FORBIDDEN.value,
                canonical_invocation=canonical_invocation,
            )
            raise ContractError(
                ErrorCode.FORBIDDEN,
                f"capability {capability.capability_id!r} denied by policy hook",
                provider_id=registration.provider_id,
            )

        approval_decision: str | None = None
        approval_required = policy_decision is PolicyDecision.REQUIRE_APPROVAL or bool(
            capability.required_approvals
        )
        if approval_required:
            if canonical_invocation is None:
                if self._governance_binding_hook is None:
                    await self._record(
                        request,
                        registration,
                        InvocationStatus.FAILED,
                        ErrorCode.CONTRACT_VIOLATION.value,
                        approval_decision="required",
                    )
                    raise ContractError(
                        ErrorCode.CONTRACT_VIOLATION,
                        (
                            f"capability {capability.capability_id!r} requires approval but no "
                            "canonical governance binding hook is configured"
                        ),
                        provider_id=registration.provider_id,
                    )
                canonical_invocation = await self._bind_invocation(
                    request,
                    registration,
                    provider_invocation,
                    self._governance_binding_hook,
                    binding_name="governance",
                    approval_decision="required",
                )

            approved = False
            if self._approval_hook is not None:
                approved = await self._approval_hook(
                    request,
                    capability,
                    canonical_invocation,
                )
            if not approved:
                await self._record(
                    request,
                    registration,
                    InvocationStatus.APPROVAL_REQUIRED,
                    ErrorCode.FORBIDDEN.value,
                    canonical_invocation=canonical_invocation,
                    approval_decision="required",
                )
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    f"capability {capability.capability_id!r} requires approval",
                    provider_id=registration.provider_id,
                    details={
                        "approval_required": True,
                        "canonical_tool_invocation_id": canonical_invocation.id,
                    },
                )
            approval_decision = "approved"

        await self._record(
            request,
            registration,
            InvocationStatus.RUNNING,
            canonical_invocation=canonical_invocation,
            approval_decision=approval_decision,
        )
        timeout = capability.timeout_seconds or request.context.control.timeout_seconds
        provider_started = perf_counter()

        try:
            if canonical_invocation is not None:
                validate_tool_invocation_binding(provider_invocation, canonical_invocation)
            if timeout is None:
                tool_result = await provider.invoke(provider_invocation)
            else:
                tool_result = await asyncio.wait_for(provider.invoke(provider_invocation), timeout)
        except TimeoutError as exc:
            adapter_metadata = self._provider_failure_metadata(
                provider,
                provider_invocation,
                error_code=ErrorCode.TIMEOUT.value,
                started=provider_started,
            )
            await self._record(
                request,
                registration,
                InvocationStatus.TIMED_OUT,
                ErrorCode.TIMEOUT.value,
                canonical_invocation=canonical_invocation,
                approval_decision=approval_decision,
                adapter_metadata=adapter_metadata,
            )
            raise ContractError(
                ErrorCode.TIMEOUT,
                f"capability {capability.capability_id!r} timed out",
                provider_id=registration.provider_id,
                retryable=True,
                adapter_metadata=adapter_metadata,
            ) from exc
        except asyncio.CancelledError as exc:
            adapter_metadata = self._provider_failure_metadata(
                provider,
                provider_invocation,
                error_code=ErrorCode.CANCELLED.value,
                started=provider_started,
            )
            await self._record(
                request,
                registration,
                InvocationStatus.CANCELLED,
                ErrorCode.CANCELLED.value,
                canonical_invocation=canonical_invocation,
                approval_decision=approval_decision,
                adapter_metadata=adapter_metadata,
            )
            raise ContractError(
                ErrorCode.CANCELLED,
                f"capability {capability.capability_id!r} was cancelled",
                provider_id=registration.provider_id,
                retryable=True,
                adapter_metadata=adapter_metadata,
            ) from exc
        except ValueError as exc:
            await self._record(
                request,
                registration,
                InvocationStatus.FAILED,
                ErrorCode.CONTRACT_VIOLATION.value,
                canonical_invocation=canonical_invocation,
                approval_decision=approval_decision,
            )
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "tool invocation governance binding changed before provider execution",
                provider_id=registration.provider_id,
            ) from exc
        except ContractError as exc:
            await self._record(
                request,
                registration,
                InvocationStatus.FAILED,
                exc.code.value,
                canonical_invocation=canonical_invocation,
                approval_decision=approval_decision,
                adapter_metadata=exc.adapter_metadata,
            )
            raise
        except Exception as exc:
            await self._record(
                request,
                registration,
                InvocationStatus.FAILED,
                ErrorCode.BACKEND_ERROR.value,
                canonical_invocation=canonical_invocation,
                approval_decision=approval_decision,
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
                canonical_invocation=canonical_invocation,
                approval_decision=approval_decision,
            )
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "tool provider returned a mismatched invocation_id",
                provider_id=registration.provider_id,
            )

        if capability.output_schema is not None:
            try:
                self._validate_schema(
                    capability.output_schema,
                    tool_result.output,
                    stage="output",
                    capability_id=capability.capability_id,
                )
            except ContractError as exc:
                await self._record(
                    request,
                    registration,
                    InvocationStatus.FAILED,
                    exc.code.value,
                    canonical_invocation=canonical_invocation,
                    approval_decision=approval_decision,
                    adapter_metadata=tool_result.adapter_metadata,
                )
                raise

        result = CapabilityInvocationResult(
            invocation_id=request.invocation_id,
            capability_id=capability.capability_id,
            capability_version=capability.version,
            provider_id=registration.provider_id,
            status=InvocationStatus.SUCCEEDED,
            output=tool_result.output,
            canonical_tool_invocation_id=(
                canonical_invocation.id if canonical_invocation is not None else None
            ),
            result_ref=tool_result.result_ref,
            artifact_refs=tool_result.artifact_refs,
            evidence_refs=tool_result.evidence_refs,
            adapter_metadata=tool_result.adapter_metadata,
        )
        await self._record(
            request,
            registration,
            InvocationStatus.SUCCEEDED,
            canonical_invocation=canonical_invocation,
            approval_decision=approval_decision,
            adapter_metadata=tool_result.adapter_metadata,
        )
        return result

    async def _bind_invocation(
        self,
        request: CapabilityInvocation,
        registration: CapabilityRegistration,
        provider_invocation: ToolInvocation,
        hook: CanonicalInvocationBindingHook | GovernanceBindingHook,
        *,
        binding_name: str,
        approval_decision: str | None = None,
    ) -> DomainToolInvocation:
        try:
            canonical_invocation = await hook(request, registration, provider_invocation)
            validate_tool_invocation_binding(provider_invocation, canonical_invocation)
        except ContractError as exc:
            await self._record(
                request,
                registration,
                InvocationStatus.FAILED,
                exc.code.value,
                approval_decision=approval_decision,
            )
            raise
        except (TypeError, ValueError) as exc:
            await self._record(
                request,
                registration,
                InvocationStatus.FAILED,
                ErrorCode.CONTRACT_VIOLATION.value,
                approval_decision=approval_decision,
            )
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                f"{binding_name} binding does not match the resolved provider invocation",
                provider_id=registration.provider_id,
            ) from exc
        return canonical_invocation

    @staticmethod
    def _provider_failure_metadata(
        provider: object,
        invocation: ToolInvocation,
        *,
        error_code: str,
        started: float,
    ) -> tuple[AdapterMetadata, ...]:
        if not isinstance(provider, InvocationFailureMetadataProvider):
            return ()
        return provider.invocation_failure_metadata(
            invocation,
            error_code=error_code,
            duration_ms=(perf_counter() - started) * 1000,
        )

    async def _record(
        self,
        request: CapabilityInvocation,
        registration: CapabilityRegistration,
        status: InvocationStatus,
        error_code: str | None = None,
        *,
        canonical_invocation: DomainToolInvocation | None = None,
        approval_decision: str | None = None,
        adapter_metadata: tuple[AdapterMetadata, ...] = (),
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
                canonical_tool_invocation_id=(
                    canonical_invocation.id if canonical_invocation is not None else None
                ),
                node_id=registration.node_id,
                worker_id=registration.worker_id,
                approval_decision=approval_decision,
                error_code=error_code,
                adapter_metadata=(*registration.adapter_metadata, *adapter_metadata),
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
                ErrorCode.INVALID_REQUEST if stage == "input" else ErrorCode.CONTRACT_VIOLATION,
                f"{stage} schema validation failed for capability {capability_id!r}: {exc.message}",
            ) from exc
