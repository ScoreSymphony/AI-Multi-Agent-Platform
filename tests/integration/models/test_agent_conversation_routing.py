from __future__ import annotations

import asyncio

from conversation_completion_cases import (
    _agent_profile,
    _collect,
    _message,
    _register_model,
    _registry,
    _response_request,
)

from ai_multi_agent_platform.agents import AgentService, InMemoryAgentRepository
from ai_multi_agent_platform.agents.models import (
    AgentModelPolicy,
    ModelFallbackPolicy,
)
from ai_multi_agent_platform.conversations import (
    ConversationResponseTarget,
    ModelRuntimeConversationResponseProvider,
)
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.models import (
    ModelRuntime,
    RoutingRequirements,
)


def test_agent_conversation_applies_routing_profile_and_fallback() -> None:
    registry, provider = _registry()
    _register_model(registry, provider, "model-small", context_window=4_096, priority=100)
    _register_model(registry, provider, "model-large", context_window=32_768, priority=1)
    agents = AgentService(InMemoryAgentRepository())
    agent = agents.create_agent(
        _agent_profile(
            AgentModelPolicy(
                requirements=RoutingRequirements(explicit_model_id="model-small"),
                routing_profile_ref="large-context",
                allow_task_override=True,
                fallback=ModelFallbackPolicy.ROUTE,
            )
        ),
        owner_ref=OwnerRef(type="user", id="alice"),
    )
    message = _message()
    request = _response_request(
        message,
        target=ConversationResponseTarget(kind="agent", id=agent.agent_id, revision=agent.revision),
    )
    responder = ModelRuntimeConversationResponseProvider(
        ModelRuntime(registry),
        agents,
        routing_profiles={"large-context": RoutingRequirements(min_context_window=16_000)},
    )

    chunks = asyncio.run(_collect(responder.stream_response(request)))

    assert chunks[-1].model_config_id == "model-large"
    assert provider.calls[-1].requirements["model_config_id"] == "model-large"
    assert provider.calls[-1].requirements["min_context_window"] == 16_000
