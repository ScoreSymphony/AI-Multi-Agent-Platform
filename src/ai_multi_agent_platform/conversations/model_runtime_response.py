"""Canonical ModelRuntime-backed conversational response provider for issue #72."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable

from ai_multi_agent_platform.agents import AgentService
from ai_multi_agent_platform.agents.models import AgentRevision, AgentTeamRevision, InstructionSource
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.models import (
    CanonicalModelRequest,
    ModelContentBlock,
    ModelContentKind,
    ModelMessage,
    ModelRole,
    ModelRuntime,
    RoutingRequirements,
)

from .models import ContentKind, ConversationMessage, MessageRole, MessageStatus
from .responses import (
    ConversationResponseChunk,
    ConversationResponseChunkKind,
    ConversationResponseRequest,
)

type ConversationInstructionResolver = Callable[[str], str]


class ModelRuntimeConversationResponseProvider:
    """Generate conversational text through the canonical model registry/router/runtime.

    This adapter deliberately performs no Task/Run transition and owns no provider-native
    session. Agent/Team targets are resolved to their immutable canonical revisions and
    only their platform-owned instruction/model policy is supplied to ``ModelRuntime``.
    """

    def __init__(
        self,
        runtime: ModelRuntime,
        agents: AgentService,
        *,
        instruction_resolver: ConversationInstructionResolver | None = None,
    ) -> None:
        self._runtime = runtime
        self._agents = agents
        self._instruction_resolver = instruction_resolver

    def stream_response(
        self,
        request: ConversationResponseRequest,
    ) -> AsyncIterator[ConversationResponseChunk]:
        async def stream() -> AsyncIterator[ConversationResponseChunk]:
            system_instruction, agent_id, agent_requirements = self._target_context(request)
            model_config_id, routing_requirements = _effective_routing(
                agent_requirements,
                request,
            )
            response = await self._runtime.generate_canonical(
                CanonicalModelRequest(
                    request_id=request.request_id,
                    context=OperationContext(
                        correlation_id=request.correlation_id,
                        causation_id=request.source_message_id,
                        project_id=request.project_id,
                    ),
                    messages=_model_history(request.history),
                    system_instruction=system_instruction,
                    model_config_id=model_config_id,
                    agent_id=agent_id,
                    routing_requirements=routing_requirements,
                )
            )
            yield ConversationResponseChunk(
                ConversationResponseChunkKind.ACTIVITY,
                "Response generated through the canonical model runtime.",
                model_config_id=response.model_config_id,
            )
            emitted = False
            for block in response.content:
                if block.kind is ModelContentKind.TEXT and block.text:
                    emitted = True
                    yield ConversationResponseChunk(
                        ConversationResponseChunkKind.TEXT,
                        block.text,
                        model_config_id=response.model_config_id,
                    )
            if not emitted:
                raise ContractError(
                    ErrorCode.CONTRACT_VIOLATION,
                    "canonical model runtime returned no conversational text",
                )

        return stream()

    def _target_context(
        self,
        request: ConversationResponseRequest,
    ) -> tuple[str, str | None, RoutingRequirements | None]:
        target = request.target
        if target.kind == "agent":
            revision = self._agents.get_agent_revision(target.id, target.revision)
            _require_enabled_agent(revision)
            return (
                self._agent_instruction(revision),
                revision.agent_id,
                revision.profile.model.requirements,
            )
        if target.kind == "agent_team":
            team = self._agents.get_team_revision(target.id, target.revision)
            if not team.profile.enabled:
                raise ContractError(
                    ErrorCode.UNAVAILABLE,
                    f"agent team is disabled: {team.team_id}@{team.revision}",
                )
            leader = self._team_leader(team)
            _require_enabled_agent(leader)
            members = ", ".join(
                f"{member.role}:{member.agent.agent_id}@{member.agent.revision}"
                for member in team.profile.members
            )
            team_context = (
                f"You are the conversational representative of canonical Agent Team "
                f"{team.profile.name} ({team.team_id}@{team.revision}). "
                f"Team members: {members}."
            )
            if team.profile.description:
                team_context = f"{team_context}\nTeam description: {team.profile.description}"
            return (
                f"{team_context}\n\n{self._agent_instruction(leader)}",
                leader.agent_id,
                leader.profile.model.requirements,
            )
        if target.kind == "project":
            return (
                f"Respond in the context of canonical Project {target.id}. Do not treat chat as "
                "durable execution state; privileged work must use canonical Task commands.",
                None,
                None,
            )
        if target.kind == "task":
            return (
                f"Respond about canonical Task {target.id}. The Task/Run lifecycle is authoritative; "
                "conversation text must not claim to mutate lifecycle state.",
                None,
                None,
            )
        return (
            "Respond as the platform conversational entrypoint. Durable work must remain in "
            "canonical Tasks/Runs and privileged actions require explicit platform commands.",
            None,
            None,
        )

    def _team_leader(self, team: AgentTeamRevision) -> AgentRevision:
        leader_id = team.profile.leader_agent_id
        selected = None
        if leader_id is not None:
            selected = next(
                (member for member in team.profile.members if member.agent.agent_id == leader_id),
                None,
            )
        if selected is None:
            selected = team.profile.members[0]
        return self._agents.get_agent_revision(
            selected.agent.agent_id,
            selected.agent.revision,
        )

    def _agent_instruction(self, revision: AgentRevision) -> str:
        source = revision.profile.instructions.role
        role_instruction = self._resolve_instruction(source)
        prefix = (
            f"You are canonical Agent {revision.profile.name} "
            f"({revision.agent_id}@{revision.revision}), role: {revision.profile.role}."
        )
        if revision.profile.description:
            prefix = f"{prefix}\nAgent description: {revision.profile.description}"
        return f"{prefix}\n\n{role_instruction}"

    def _resolve_instruction(self, source: InstructionSource) -> str:
        if source.content is not None:
            return source.content
        assert source.ref is not None
        if self._instruction_resolver is None:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "agent conversational instruction reference has no configured resolver",
                details={"instruction_ref": source.ref},
            )
        resolved = self._instruction_resolver(source.ref)
        if not resolved.strip():
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "agent conversational instruction resolver returned blank content",
                details={"instruction_ref": source.ref},
            )
        return resolved


def _require_enabled_agent(revision: AgentRevision) -> None:
    if not revision.profile.enabled:
        raise ContractError(
            ErrorCode.UNAVAILABLE,
            f"agent is disabled: {revision.agent_id}@{revision.revision}",
        )


def _effective_routing(
    agent_requirements: RoutingRequirements | None,
    request: ConversationResponseRequest,
) -> tuple[str | None, dict[str, JsonValue]]:
    requirements = _routing_requirements_json(agent_requirements)
    model_config_id = agent_requirements.explicit_model_id if agent_requirements is not None else None
    preference = request.model_preference
    if preference is not None:
        requirements.update(dict(preference.routing_requirements))
        if preference.model_config_id is not None:
            model_config_id = preference.model_config_id
    return model_config_id, requirements


def _routing_requirements_json(
    requirements: RoutingRequirements | None,
) -> dict[str, JsonValue]:
    if requirements is None:
        return {}
    result: dict[str, JsonValue] = {}
    if requirements.min_context_window is not None:
        result["min_context_window"] = requirements.min_context_window
    for key, value in (
        ("tool_calling", requirements.tool_calling),
        ("structured_output", requirements.structured_output),
        ("streaming", requirements.streaming),
        ("local_only", requirements.local_only),
        ("self_hosted_only", requirements.self_hosted_only),
    ):
        if value:
            result[key] = value
    if requirements.modalities:
        result["modalities"] = list(requirements.modalities)
    if requirements.reasoning:
        result["reasoning"] = list(requirements.reasoning)
    return result


def _model_history(history: tuple[ConversationMessage, ...]) -> tuple[ModelMessage, ...]:
    messages: list[ModelMessage] = []
    for message in history:
        if message.status is MessageStatus.TOMBSTONED:
            messages.append(ModelMessage.text(ModelRole.SYSTEM, "[Conversation message redacted]"))
            continue
        role = _model_role(message.role)
        blocks = _model_content(message)
        if role is ModelRole.TOOL:
            role = ModelRole.SYSTEM
            blocks = (
                ModelContentBlock(ModelContentKind.TEXT, text="[Tool message]"),
                *blocks,
            )
        messages.append(ModelMessage(role=role, content=blocks))
    return tuple(messages)


def _model_role(role: MessageRole) -> ModelRole:
    if role is MessageRole.USER:
        return ModelRole.USER
    if role is MessageRole.ASSISTANT:
        return ModelRole.ASSISTANT
    if role is MessageRole.TOOL:
        return ModelRole.TOOL
    return ModelRole.SYSTEM


def _model_content(message: ConversationMessage) -> tuple[ModelContentBlock, ...]:
    blocks: list[ModelContentBlock] = []
    seen_refs: set[tuple[str, str]] = set()
    for block in message.content:
        if block.kind in {ContentKind.TEXT, ContentKind.MARKDOWN} and block.text is not None:
            blocks.append(ModelContentBlock(ModelContentKind.TEXT, text=block.text))
        elif block.kind is ContentKind.JSON and block.value is not None:
            blocks.append(ModelContentBlock(ModelContentKind.JSON, value=block.value))
        elif block.reference is not None:
            reference = block.reference
            seen_refs.add((reference.kind.value, reference.id))
            blocks.append(
                ModelContentBlock(
                    ModelContentKind.TEXT,
                    text=f"[Canonical reference {reference.kind.value}:{reference.id}]",
                )
            )
    remaining = [
        reference
        for reference in message.references
        if (reference.kind.value, reference.id) not in seen_refs
    ]
    if remaining:
        blocks.append(
            ModelContentBlock(
                ModelContentKind.TEXT,
                text="Canonical references: "
                + ", ".join(f"{item.kind.value}:{item.id}" for item in remaining),
            )
        )
    if not blocks:
        blocks.append(
            ModelContentBlock(
                ModelContentKind.TEXT,
                text=json.dumps({"message_id": message.id}, separators=(",", ":")),
            )
        )
    return tuple(blocks)


__all__ = [
    "ConversationInstructionResolver",
    "ModelRuntimeConversationResponseProvider",
]
