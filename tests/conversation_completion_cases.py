from __future__ import annotations

from collections.abc import AsyncIterator

from ai_multi_agent_platform.agents.models import (
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import (
    HealthStatus,
    OperationContext,
)
from ai_multi_agent_platform.conversations import (
    ConversationContentBlock,
    ConversationMessage,
    ConversationResponseChunk,
    ConversationResponseRequest,
    ConversationResponseTarget,
    MessageRole,
    ResourceReference,
)
from ai_multi_agent_platform.data import (
    DataAccessContext,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.models import (
    ModelCapabilities,
    ModelConfiguration,
    ModelRegistry,
)
from ai_multi_agent_platform.testing import FakeModelProvider


async def _collect(
    stream: AsyncIterator[ConversationResponseChunk],
) -> tuple[ConversationResponseChunk, ...]:
    return tuple([item async for item in stream])


def _message(
    *,
    conversation_id: str | None = None,
    text: str = "Use the attached canonical context.",
    references: tuple[ResourceReference, ...] = (),
) -> ConversationMessage:
    return ConversationMessage(
        conversation_id=conversation_id or new_id("conversation"),
        sender_ref="user:alice",
        role=MessageRole.USER,
        content=(ConversationContentBlock.text_block(text),),
        references=references,
    )


def _response_request(
    message: ConversationMessage,
    *,
    target: ConversationResponseTarget | None = None,
    project_id: str | None = None,
) -> ConversationResponseRequest:
    return ConversationResponseRequest(
        request_id="request-issue-72-audit",
        correlation_id="correlation-issue-72-audit",
        actor_ref="user:alice",
        conversation_id=message.conversation_id,
        source_message_id=message.id,
        target=target or ConversationResponseTarget(kind="orchestrator", id="platform"),
        history=(message,),
        project_id=project_id,
    )


def _data_context(project_id: str) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="issue-72-context-setup",
            owner_type="user",
            owner_id="alice",
            project_id=project_id,
        ),
        actor_ref="user:alice",
    )


def _registry() -> tuple[ModelRegistry, FakeModelProvider]:
    provider = FakeModelProvider(response_text="Canonical answer")
    registry = ModelRegistry()
    registry.register_provider(provider)
    return registry, provider


def _register_model(
    registry: ModelRegistry,
    provider: FakeModelProvider,
    config_id: str,
    *,
    context_window: int,
    priority: int = 0,
) -> None:
    registry.register_model(
        ModelConfiguration(
            config_id=config_id,
            display_name=config_id,
            provider_id=provider.descriptor.provider_id,
            capabilities=ModelCapabilities(context_window=context_window, modalities=("text",)),
            health=HealthStatus.HEALTHY,
            priority=priority,
        )
    )


def _agent_profile(model: AgentModelPolicy) -> AgentProfile:
    return AgentProfile(
        name="Conversation Agent",
        role="assistant",
        instructions=AgentInstructions(
            role=InstructionSource(content="Answer through canonical policy.", version="1")
        ),
        model=model,
    )
