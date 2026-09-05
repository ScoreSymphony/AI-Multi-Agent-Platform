"""Canonical ModelRuntime-backed conversational response provider for issue #72."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Mapping
from dataclasses import replace

from ai_multi_agent_platform.agents import AgentService
from ai_multi_agent_platform.agents.models import (
    AgentModelPolicy,
    AgentRevision,
    AgentTeamRevision,
    InstructionSource,
    ModelFallbackPolicy,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
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

from .models import ContentKind, ConversationMessage, MessageRole, MessageStatus
from .responses import (
    ConversationResolvedContext,
    ConversationResponseChunk,
    ConversationResponseChunkKind,
    ConversationResponseRequest,
)

type ConversationInstructionResolver = Callable[[str], str]


class ModelRuntimeConversationResponseProvider:
    """Generate conversational text through the canonical model registry/router/runtime.

    Conversational output is deliberately non-durable execution: it creates no Task/Run
    or AgentRun by itself. Agent/Team targets still use their exact immutable revision and
    canonical model-routing policy. When the user turns the conversation into durable
    work, the Conversation service assigns that exact Agent/Team revision to the canonical
    Task so normal AgentRuntime capability/authorization policy remains authoritative.
    """

    def __init__(
        self,
        runtime: ModelRuntime,
        agents: AgentService,
        *,
        instruction_resolver: ConversationInstructionResolver | None = None,
        routing_profiles: Mapping[str, RoutingRequirements] | None = None,
    ) -> None:
        self._runtime = runtime
        self._agents = agents
        self._instruction_resolver = instruction_resolver
        self._routing_profiles = dict(routing_profiles or {})

    def stream_response(
        self,
        request: ConversationResponseRequest,
    ) -> AsyncIterator[ConversationResponseChunk]:
        async def stream() -> AsyncIterator[ConversationResponseChunk]:
            system_instruction, agent_id, agent_policy = self._target_context(request)
            model_config_id, routing_requirements = self._effective_routing(
                agent_policy,
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
                    messages=(
                        *_resolved_context_messages(request.resolved_context),
                        *_model_history(request.history),
                    ),
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
    ) -> tuple[str, str | None, AgentModelPolicy | None]:
        target = request.target
        if target.kind == "agent":
            revision = self._agents.get_agent_revision(target.id, target.revision)
            _require_enabled_agent(revision)
            return (
                self._agent_instruction(revision),
                revision.agent_id,
                revision.profile.model,
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
                leader.profile.model,
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
                f"Respond about canonical Task {target.id}. The Task/Run lifecycle is "
                "authoritative; conversation text must not claim to mutate lifecycle state.",
                None,
                None,
            )
        return (
            "Respond as the platform conversational entrypoint. Durable work must remain in "
            "canonical Tasks/Runs and privileged actions require explicit platform commands.",
            None,
            None,
        )

    def _effective_routing(
        self,
        agent_policy: AgentModelPolicy | None,
        request: ConversationResponseRequest,
    ) -> tuple[str | None, dict[str, JsonValue]]:
        preference = request.model_preference
        if agent_policy is None:
            if preference is None:
                return None, {}
            return preference.model_config_id, dict(preference.routing_requirements)

        requirements = self._agent_requirements(agent_policy)
        if preference is not None and (
            preference.model_config_id is not None or preference.routing_requirements
        ):
            if not agent_policy.allow_task_override:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "conversation model override is not permitted by this Agent revision",
                )
            requirements = _merge_requirements(
                requirements,
                _preference_requirements(
                    preference.model_config_id, preference.routing_requirements
                ),
            )

        router = DeterministicModelRouter(self._runtime.registry)
        try:
            route = router.route(requirements)
        except ContractError:
            if (
                requirements.explicit_model_id is None
                or agent_policy.fallback is not ModelFallbackPolicy.ROUTE
            ):
                raise
            requirements = replace(requirements, explicit_model_id=None)
            route = router.route(requirements)
        return route.model_config_id, _routing_requirements_json(requirements)

    def _agent_requirements(self, policy: AgentModelPolicy) -> RoutingRequirements:
        requirements = policy.requirements
        profile_ref = policy.routing_profile_ref
        if profile_ref is None:
            return requirements
        try:
            profile = self._routing_profiles[profile_ref]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                f"agent routing profile is not configured: {profile_ref}",
            ) from exc
        return _merge_requirements(profile, requirements)

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


def _preference_requirements(
    model_config_id: str | None,
    values: Mapping[str, JsonValue],
) -> RoutingRequirements:
    supported = {
        "min_context_window",
        "tool_calling",
        "structured_output",
        "streaming",
        "modalities",
        "reasoning",
        "local_only",
        "self_hosted_only",
    }
    unknown = sorted(set(values).difference(supported))
    if unknown:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "conversation model preference contains unsupported routing requirements",
            details={"fields": unknown},
        )

    def optional_positive_int(key: str) -> int | None:
        value = values.get(key)
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a positive integer")
        return value

    def boolean(key: str) -> bool:
        value = values.get(key, False)
        if not isinstance(value, bool):
            raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a boolean")
        return value

    def strings(key: str) -> tuple[str, ...]:
        value = values.get(key, [])
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a list of strings")
        return tuple(value)

    return RoutingRequirements(
        explicit_model_id=model_config_id,
        min_context_window=optional_positive_int("min_context_window"),
        tool_calling=boolean("tool_calling"),
        structured_output=boolean("structured_output"),
        streaming=boolean("streaming"),
        modalities=strings("modalities"),
        reasoning=strings("reasoning"),
        local_only=boolean("local_only"),
        self_hosted_only=boolean("self_hosted_only"),
    )


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
            "combined Agent model requirements conflict: local_only and self_hosted_only",
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


def _resolved_context_messages(
    resolved: tuple[ConversationResolvedContext, ...],
) -> tuple[ModelMessage, ...]:
    return tuple(
        ModelMessage.text(
            ModelRole.SYSTEM,
            f"Authorized canonical conversation context {item.kind}:{item.id}:\n{item.text}",
        )
        for item in resolved
    )


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
