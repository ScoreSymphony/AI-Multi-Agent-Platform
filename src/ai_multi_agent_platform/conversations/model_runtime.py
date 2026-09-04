"""ModelRuntime-backed conversational response provider for issue #72.

This adapter is platform-owned and provider-neutral. It translates durable Conversation
history plus an exact canonical Agent/Team target into the existing #10 model protocol;
it never exposes provider-native session identifiers and never executes privileged work.
"""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.agents.models import (
    AgentRevision,
    AgentTeamRevision,
    ModelFallbackPolicy,
)
from ai_multi_agent_platform.agents.runtime import AgentRuntime
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, ModelRequest
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.models import (
    CanonicalModelRequest,
    DeterministicModelRouter,
    ModelContentBlock,
    ModelContentKind,
    ModelMessage,
    ModelRole,
    ModelRuntime,
    RoutingRequirements,
)

from .models import ContentKind, ConversationMessage, MessageRole, ReferenceKind, ResourceReference
from .responses import (
    ConversationResponseChunk,
    ConversationResponseChunkKind,
    ConversationResponseRequest,
)

_BASE_SYSTEM_INSTRUCTION = (
    "You are responding through the platform's canonical Conversation interface. "
    "Conversation text and model output are not authorization. Do not claim that privileged "
    "actions, tool calls, Task transitions, approvals, file changes, or external side effects "
    "occurred unless they are represented by canonical platform resources supplied to you."
)


class ModelRuntimeConversationResponseProvider:
    """Generate Conversation responses through the replaceable canonical #10 ModelRuntime."""

    def __init__(
        self,
        runtime: ModelRuntime,
        *,
        agent_runtime: AgentRuntime | None = None,
    ) -> None:
        self._runtime = runtime
        self._agent_runtime = agent_runtime

    def stream_response(
        self,
        request: ConversationResponseRequest,
    ):
        async def stream():
            agent, team, requirements = self._resolve_target_policy(request)
            route = self._route(requirements, agent)
            model_request = CanonicalModelRequest(
                request_id=request.request_id,
                context=request.operation,
                messages=_history_messages(request.history),
                system_instruction=_system_instruction(request, agent=agent, team=team),
                model_config_id=route.model_config_id,
                agent_id=agent.agent_id if agent is not None else None,
                routing_requirements=_routing_requirements_json(requirements),
            )
            response = await self._runtime.generate_canonical(model_request)
            text = "".join(
                block.text or ""
                for block in response.content
                if block.kind is ModelContentKind.TEXT
            )
            if not text:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "canonical ModelRuntime completed without conversational text",
                )
            yield ConversationResponseChunk(
                kind=ConversationResponseChunkKind.TEXT,
                text=text,
                model_config_id=response.model_config_id,
            )

        return stream()

    def _resolve_target_policy(
        self,
        request: ConversationResponseRequest,
    ) -> tuple[AgentRevision | None, AgentTeamRevision | None, RoutingRequirements]:
        preference = _conversation_requirements(request)
        target = request.target
        if target.kind not in {"agent", "agent_team"}:
            return None, None, preference

        runtime = self._agent_runtime
        if runtime is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "canonical AgentRuntime is not configured for conversational Agent routing",
                retryable=True,
            )

        if target.kind == "agent":
            agent = runtime.service.get_agent_revision(target.id, target.revision)
            _require_enabled(agent)
            return agent, None, _merge_agent_and_conversation_requirements(runtime, agent, preference)

        team = runtime.service.get_team_revision(target.id, target.revision)
        if not team.profile.enabled:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"agent team is disabled: {team.team_id}@{team.revision}",
            )
        leader_id = team.profile.leader_agent_id or team.profile.members[0].agent.agent_id
        member = next(item for item in team.profile.members if item.agent.agent_id == leader_id)
        agent = runtime.service.get_agent_revision(member.agent.agent_id, member.agent.revision)
        _require_enabled(agent)
        requirements = _merge_agent_and_conversation_requirements(runtime, agent, preference)
        return agent, team, requirements

    def _route(
        self,
        requirements: RoutingRequirements,
        agent: AgentRevision | None,
    ):
        router = DeterministicModelRouter(self._runtime.registry)
        try:
            return router.route(requirements)
        except ContractError:
            if (
                agent is None
                or requirements.explicit_model_id is None
                or agent.profile.model.fallback is not ModelFallbackPolicy.ROUTE
            ):
                raise
            return router.route(replace(requirements, explicit_model_id=None))


def _conversation_requirements(request: ConversationResponseRequest) -> RoutingRequirements:
    preference = request.model_preference
    values: dict[str, JsonValue] = {}
    if preference is not None:
        values.update(preference.routing_requirements)
        if preference.model_config_id is not None:
            values["model_config_id"] = preference.model_config_id
    baseline = ModelRequest(
        request_id=f"{request.request_id}:routing",
        messages=("conversation routing",),
        context=request.operation,
        requirements=values,
    )
    try:
        return RoutingRequirements.from_request(baseline)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"invalid conversation model routing requirements: {exc}",
        ) from exc


def _merge_agent_and_conversation_requirements(
    runtime: AgentRuntime,
    agent: AgentRevision,
    conversation: RoutingRequirements,
) -> RoutingRequirements:
    requirements = agent.profile.model.requirements
    profile_ref = agent.profile.model.routing_profile_ref
    if profile_ref is not None:
        try:
            requirements = _merge_requirements(runtime.routing_profiles[profile_ref], requirements)
        except KeyError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                f"agent routing profile is not configured: {profile_ref}",
            ) from exc
    return _merge_requirements(requirements, conversation)


def _merge_requirements(
    base: RoutingRequirements,
    overlay: RoutingRequirements,
) -> RoutingRequirements:
    context_windows = [
        value
        for value in (base.min_context_window, overlay.min_context_window)
        if value is not None
    ]
    modalities = tuple(dict.fromkeys((*base.modalities, *overlay.modalities)))
    reasoning = tuple(dict.fromkeys((*base.reasoning, *overlay.reasoning)))
    local_only = base.local_only or overlay.local_only
    self_hosted_only = base.self_hosted_only or overlay.self_hosted_only
    if local_only and self_hosted_only:
        raise ContractError(
            ErrorCode.INVALID_CONFIGURATION,
            "combined conversational model requirements conflict: local_only and self_hosted_only",
        )
    return RoutingRequirements(
        explicit_model_id=overlay.explicit_model_id or base.explicit_model_id,
        min_context_window=max(context_windows) if context_windows else None,
        tool_calling=base.tool_calling or overlay.tool_calling,
        structured_output=base.structured_output or overlay.structured_output,
        streaming=base.streaming or overlay.streaming,
        modalities=modalities,
        reasoning=reasoning,
        local_only=local_only,
        self_hosted_only=self_hosted_only,
    )


def _routing_requirements_json(requirements: RoutingRequirements) -> dict[str, JsonValue]:
    values: dict[str, JsonValue] = {}
    if requirements.min_context_window is not None:
        values["min_context_window"] = requirements.min_context_window
    if requirements.tool_calling:
        values["tool_calling"] = True
    if requirements.structured_output:
        values["structured_output"] = True
    if requirements.streaming:
        values["streaming"] = True
    if requirements.modalities:
        values["modalities"] = list(requirements.modalities)
    if requirements.reasoning:
        values["reasoning"] = list(requirements.reasoning)
    if requirements.local_only:
        values["local_only"] = True
    if requirements.self_hosted_only:
        values["self_hosted_only"] = True
    return values


def _require_enabled(agent: AgentRevision) -> None:
    if not agent.profile.enabled:
        raise ContractError(
            ErrorCode.UNAVAILABLE,
            f"agent is disabled: {agent.agent_id}@{agent.revision}",
        )


def _system_instruction(
    request: ConversationResponseRequest,
    *,
    agent: AgentRevision | None,
    team: AgentTeamRevision | None,
) -> str:
    parts = [_BASE_SYSTEM_INSTRUCTION]
    if team is not None:
        parts.append(
            f"You are the conversational representative of Agent Team {team.profile.name} "
            f"({team.team_id}@{team.revision})."
        )
    if agent is not None:
        parts.append(
            f"Use the exact canonical Agent revision {agent.agent_id}@{agent.revision}; "
            f"profile role: {agent.profile.role}."
        )
        instruction = agent.profile.instructions.role
        if instruction.content is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "the selected Agent uses ref-backed role instructions that the conversational "
                "ModelRuntime responder cannot resolve",
                retryable=False,
                details={"instruction_ref": instruction.ref},
            )
        parts.append(instruction.content)
    else:
        parts.append(
            f"Respond in the canonical {request.target.kind} context {request.target.id}."
        )
    return "\n\n".join(parts)


def _history_messages(history: tuple[ConversationMessage, ...]) -> tuple[ModelMessage, ...]:
    messages: list[ModelMessage] = []
    for message in history:
        role = _model_role(message.role)
        blocks: list[ModelContentBlock] = []
        for content in message.content:
            if content.kind in {ContentKind.TEXT, ContentKind.MARKDOWN} and content.text:
                blocks.append(ModelContentBlock(ModelContentKind.TEXT, text=content.text))
            elif content.kind is ContentKind.JSON and content.value is not None:
                blocks.append(ModelContentBlock(ModelContentKind.JSON, value=content.value))
            elif content.reference is not None:
                blocks.append(_reference_block(content.reference))
        embedded = {
            (content.reference.kind, content.reference.id)
            for content in message.content
            if content.reference is not None
        }
        for reference in message.references:
            if (reference.kind, reference.id) not in embedded:
                blocks.append(_reference_block(reference))
        if not blocks:
            blocks.append(ModelContentBlock(ModelContentKind.TEXT, text="[empty durable message]"))
        messages.append(ModelMessage(role=role, content=tuple(blocks)))
    return tuple(messages)


def _reference_block(reference: ResourceReference) -> ModelContentBlock:
    if reference.kind is ReferenceKind.FILE:
        return ModelContentBlock(ModelContentKind.FILE_REF, ref=reference.id)
    return ModelContentBlock(
        ModelContentKind.JSON,
        value={
            "canonical_reference": {
                "kind": reference.kind.value,
                "id": reference.id,
                "label": reference.label,
            }
        },
    )


def _model_role(role: MessageRole) -> ModelRole:
    if role is MessageRole.USER:
        return ModelRole.USER
    if role is MessageRole.SYSTEM:
        return ModelRole.SYSTEM
    if role is MessageRole.ASSISTANT:
        return ModelRole.ASSISTANT
    # Conversation tool/event messages are durable context, but they are not ModelProtocol
    # tool-result messages because the Conversation contract intentionally stores no private
    # tool_call_id. Present them as system context rather than fabricating a tool call.
    return ModelRole.SYSTEM


__all__ = ["ModelRuntimeConversationResponseProvider"]
