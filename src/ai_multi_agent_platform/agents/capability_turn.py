"""One canonical Agent model turn with backend-neutral capability execution.

This module intentionally composes existing platform seams instead of teaching the Agent
runtime about provider-native tool APIs. The model receives canonical capability definitions,
may request one or more tool calls, and those requests are executed through ``CapabilityInvoker``.
The reference turn is deliberately conservative: only standard, side-effect-free,
credential-free capabilities without approval requirements execute here. Governed/sensitive
actions stay fail-closed until the canonical approval binding is composed into this path.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, replace
from hashlib import sha256

from ai_multi_agent_platform.capabilities import (
    CapabilityInvocation,
    CapabilityInvoker,
    CapabilityRegistry,
    CapabilitySpec,
    CredentialRequirement,
    InvocationTrace,
    SafetyClassification,
    SideEffectClassification,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue, OperationContext
from ai_multi_agent_platform.models import (
    CanonicalModelRequest,
    CanonicalModelResponse,
    ModelContentKind,
    ModelMessage,
    ModelRole,
    ModelRuntime,
    ModelToolDefinition,
)


@dataclass(frozen=True, slots=True)
class AgentCapabilityTurnResult:
    """Normalized evidence produced by one model turn and its requested capability calls."""

    text: str
    model_ref: str
    model_call_refs: tuple[str, ...]
    tool_invocation_refs: tuple[str, ...]
    artifact_refs: tuple[str, ...]
    capability_results: tuple[dict[str, JsonValue], ...]
    model_usage: dict[str, JsonValue]


class AgentCapabilityTurn:
    """Execute one rich model turn and any capability requests it emits."""

    def __init__(
        self,
        models: ModelRuntime,
        registry: CapabilityRegistry,
        invoker: CapabilityInvoker,
    ) -> None:
        self._models = models
        self._registry = registry
        self._invoker = invoker

    async def execute(
        self,
        *,
        task_id: str,
        run_id: str,
        agent_id: str,
        model_config_id: str,
        instruction: str,
        objective: str,
        capability_ids: tuple[str, ...],
        capability_versions: dict[str, str],
        context: OperationContext,
    ) -> AgentCapabilityTurnResult:
        tools, tool_names = self._tool_definitions(
            capability_ids,
            capability_versions=capability_versions,
        )
        response = await self._models.generate_canonical(
            CanonicalModelRequest(
                request_id=f"{run_id}:model",
                context=context,
                system_instruction=instruction,
                messages=(ModelMessage.text(ModelRole.USER, objective or "Execute the task."),),
                tools=tools,
                model_config_id=model_config_id,
                task_id=task_id,
                run_id=run_id,
                agent_id=agent_id,
                routing_requirements={
                    "modalities": ["text"],
                    "tool_calling": bool(tools),
                },
            )
        )

        invocation_refs: list[str] = []
        artifact_refs: list[str] = []
        results: list[dict[str, JsonValue]] = []
        for call in response.tool_calls:
            capability_id = tool_names.get(call.tool_name)
            if capability_id is None:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    f"model requested an unadvertised capability tool: {call.tool_name}",
                    details={"tool_name": call.tool_name},
                )
            version = capability_versions.get(capability_id)
            if version is None:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    f"AgentRun did not pin a version for capability {capability_id!r}",
                )
            invocation_id = f"{run_id}:{call.call_id}"
            operation = replace(context, causation_id=response.request_id)
            result = await self._invoker.invoke(
                CapabilityInvocation(
                    invocation_id=invocation_id,
                    capability_id=capability_id,
                    version=version,
                    arguments=call.arguments,
                    context=operation,
                    trace=InvocationTrace(
                        correlation_id=operation.correlation_id,
                        task_id=task_id,
                        run_id=run_id,
                        agent_id=agent_id,
                        project_id=operation.project_id,
                        causation_id=operation.causation_id,
                    ),
                )
            )
            invocation_refs.append(result.canonical_tool_invocation_id or result.invocation_id)
            artifact_refs.extend(result.artifact_refs)
            results.append(
                {
                    "invocation_id": result.invocation_id,
                    "capability_id": result.capability_id,
                    "capability_version": result.capability_version,
                    "provider_id": result.provider_id,
                    "status": result.status.value,
                    "output": result.output,
                    "result_ref": result.result_ref,
                    "artifact_refs": list(result.artifact_refs),
                    "evidence_refs": list(result.evidence_refs),
                }
            )

        return AgentCapabilityTurnResult(
            text=_response_text(response, results),
            model_ref=response.model_config_id,
            model_call_refs=(response.request_id,),
            tool_invocation_refs=tuple(invocation_refs),
            artifact_refs=tuple(dict.fromkeys(artifact_refs)),
            capability_results=tuple(results),
            model_usage=dict(response.usage),
        )

    def _tool_definitions(
        self,
        capability_ids: tuple[str, ...],
        *,
        capability_versions: dict[str, str],
    ) -> tuple[tuple[ModelToolDefinition, ...], dict[str, str]]:
        definitions: list[ModelToolDefinition] = []
        names: dict[str, str] = {}
        for capability_id in capability_ids:
            version = capability_versions.get(capability_id)
            if version is None:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    f"AgentRun did not pin a version for capability {capability_id!r}",
                )
            registration, _ = self._registry.resolve(capability_id, version=version)
            capability = registration.capability
            _ensure_reference_safe(capability)
            name = _model_tool_name(capability_id)
            if name in names and names[name] != capability_id:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "multiple canonical capabilities map to the same model tool name",
                    details={"tool_name": name},
                )
            names[name] = capability_id
            definitions.append(
                ModelToolDefinition(
                    tool_ref=capability_id,
                    name=name,
                    description=capability.description,
                    input_schema=dict(capability.input_schema),
                )
            )
        return tuple(definitions), names


def _ensure_reference_safe(capability: CapabilitySpec) -> None:
    if (
        capability.safety is not SafetyClassification.STANDARD
        or capability.side_effects is not SideEffectClassification.NONE
        or capability.credential_requirement is not CredentialRequirement.NONE
        or bool(capability.required_approvals)
    ):
        raise ContractError(
            ErrorCode.FORBIDDEN,
            (
                f"reference Agent capability execution is limited to standard, side-effect-free "
                f"capabilities without credentials/approvals: {capability.capability_id!r}"
            ),
            details={"governed_capability_requires_composed_approval_path": True},
        )


def _model_tool_name(capability_id: str) -> str:
    readable = re.sub(r"[^A-Za-z0-9_-]+", "_", capability_id).strip("_") or "capability"
    digest = sha256(capability_id.encode("utf-8")).hexdigest()[:8]
    return f"{readable[:48]}_{digest}"


def _response_text(
    response: CanonicalModelResponse,
    capability_results: list[dict[str, JsonValue]],
) -> str:
    text = "\n".join(
        block.text or ""
        for block in response.content
        if block.kind is ModelContentKind.TEXT and block.text
    )
    if text:
        return text
    if capability_results:
        return json.dumps(capability_results, sort_keys=True, separators=(",", ":"))
    if response.structured_output is not None:
        return json.dumps(response.structured_output, sort_keys=True, separators=(",", ":"))
    return ""


__all__ = ["AgentCapabilityTurn", "AgentCapabilityTurnResult"]
