"""Agent, conversation and model runtime construction for single-node deployment."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from ai_multi_agent_platform.agents import (
    AgentRuntime,
    AgentService,
    DurableRoutingProfileAgentRuntime,
    JsonAgentRepository,
)
from ai_multi_agent_platform.capabilities import CapabilityRegistry
from ai_multi_agent_platform.conversations import (
    ConversationService,
    DurableRoutingProfileConversationResponseProvider,
    JsonConversationRepository,
)
from ai_multi_agent_platform.models import (
    JsonModelRegistryStore,
    JsonModelRoutingProfileRepository,
    ModelRegistry,
    ModelRoutingProfileAssignmentGate,
    ModelRoutingProfileService,
    ModelRuntime,
)
from ai_multi_agent_platform.onboarding import (
    JsonModelProviderSetupStore,
    JsonOnboardingCommandStore,
    OnboardingModelAdapter,
    OnboardingService,
)

from ..config import SingleNodeConfig
from .security import SecurityBundle
from .storage import StorageBundle


@dataclass(frozen=True, slots=True)
class RuntimeServicesBundle:
    """Core agent/model authorities required by execution and platform services."""

    agents: AgentService
    conversations: ConversationService
    capabilities: CapabilityRegistry
    models: ModelRegistry
    routing_profile_repository: JsonModelRoutingProfileRepository
    routing_profiles: ModelRoutingProfileService
    routing_profile_assignment_gate: ModelRoutingProfileAssignmentGate
    agent_runtime: AgentRuntime
    onboarding: OnboardingService
    model_runtime: ModelRuntime
    conversation_response_provider: DurableRoutingProfileConversationResponseProvider


def build_runtime_services(
    config: SingleNodeConfig,
    storage: StorageBundle,
    security: SecurityBundle,
    *,
    onboarding_model_adapters: Iterable[OnboardingModelAdapter] = (),
) -> RuntimeServicesBundle:
    """Build durable agent, conversation and model services without kernel ownership."""

    database_dir = config.database_dir
    agents = AgentService(JsonAgentRepository(database_dir / "agents.json"))
    conversations = ConversationService(
        JsonConversationRepository(database_dir / "conversations.json")
    )
    capabilities = CapabilityRegistry()
    models = ModelRegistry()
    routing_profile_repository = JsonModelRoutingProfileRepository(
        database_dir / "model-routing-profiles.json"
    )
    agent_runtime = DurableRoutingProfileAgentRuntime(
        agents,
        routing_profile_repository=routing_profile_repository,
        model_registry=models,
        capability_registry=capabilities,
    )
    onboarding = OnboardingService(
        models=models,
        model_store=JsonModelRegistryStore(database_dir / "models.json"),
        provider_store=JsonModelProviderSetupStore(database_dir / "model-providers.json"),
        command_store=JsonOnboardingCommandStore(database_dir / "onboarding-commands.json"),
        scopes=storage.scopes,
        agents=agents,
        agent_runtime=agent_runtime,
        model_adapters=onboarding_model_adapters,
    )
    onboarding.restore()
    model_runtime = ModelRuntime(models)
    conversation_response_provider = DurableRoutingProfileConversationResponseProvider(
        model_runtime,
        agents,
        routing_profile_repository=routing_profile_repository,
    )
    routing_profiles = ModelRoutingProfileService(
        routing_profile_repository,
        authorization=security.authorization,
    )
    routing_profile_assignment_gate = ModelRoutingProfileAssignmentGate(
        routing_profile_repository,
        authorization=security.authorization,
    )
    return RuntimeServicesBundle(
        agents=agents,
        conversations=conversations,
        capabilities=capabilities,
        models=models,
        routing_profile_repository=routing_profile_repository,
        routing_profiles=routing_profiles,
        routing_profile_assignment_gate=routing_profile_assignment_gate,
        agent_runtime=agent_runtime,
        onboarding=onboarding,
        model_runtime=model_runtime,
        conversation_response_provider=conversation_response_provider,
    )
