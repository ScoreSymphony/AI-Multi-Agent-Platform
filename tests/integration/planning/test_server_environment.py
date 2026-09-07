from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.capabilities import (
    CapabilityRegistration,
    CapabilityRegistry,
    CapabilitySpec,
)
from ai_multi_agent_platform.capabilities.provider import CapabilityToolProvider
from ai_multi_agent_platform.contracts import (
    Capability,
    CapabilityKind,
    ContractError,
    ErrorCode,
    HealthStatus,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.control_plane.models import ActorContext, RequestContext
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.planning import (
    AgentAssignment,
    CapabilityRequirement,
    DeterministicReferencePlanner,
    InMemoryPlanningRepository,
    PlanDraft,
    PlanningEnvironment,
    PlanningService,
    PlanningStepDraft,
    PlanningTrigger,
    ProposalStatus,
)
from ai_multi_agent_platform.planning.control_plane import PlanningCommandHandlers
from ai_multi_agent_platform.security import ActorIdentity
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

OWNER = OwnerRef(type="user", id="issue-439-server-environment")


class _SecureCapabilityProvider(CapabilityToolProvider):
    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="issue-439-secure-capability",
            provider_type="test",
            capabilities=(
                Capability(
                    name="capability.secure",
                    kind=CapabilityKind.TOOL,
                    supported_operations=("invoke",),
                ),
            ),
            health=HealthStatus.HEALTHY,
        )

    async def capability_registrations(self) -> tuple[CapabilityRegistration, ...]:
        return (
            CapabilityRegistration(
                capability=CapabilitySpec(
                    capability_id="capability.secure",
                    name="Secure planning capability",
                    required_permissions=("workspace.write",),
                    required_worker_capabilities=("secure-worker",),
                    health=HealthStatus.HEALTHY,
                ),
                provider_id=self.descriptor.provider_id,
                provider_tool_ref="secure",
            ),
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        return ToolResult(invocation_id=invocation.invocation_id, output={"ok": True})


class _Resolver:
    def __init__(self, environment: PlanningEnvironment) -> None:
        self.environment = environment
        self.calls: list[tuple[str, str, str | None, PlanningTrigger]] = []

    async def resolve(
        self,
        *,
        task,
        context,
        actor: ActorIdentity,
        workspace_id: str | None,
        trigger: PlanningTrigger,
    ) -> PlanningEnvironment:
        self.calls.append((actor.actor_id, context.owner_id, workspace_id, trigger))
        assert task.task_id == context.correlation_id
        return self.environment


def _agents() -> tuple[InMemoryAgentRepository, str, int]:
    repository = InMemoryAgentRepository()
    revision = AgentService(repository).create_agent(
        AgentProfile(
            name="Server-resolved planning worker",
            role="worker",
            instructions=AgentInstructions(
                role=InstructionSource(content="Execute server-authorized work.", version="1")
            ),
        ),
        owner_ref=OWNER,
    )
    return repository, revision.agent_id, revision.revision


def _draft(agent_id: str, revision: int) -> PlanDraft:
    return PlanDraft(
        summary="Server-authorized planning",
        steps=(
            PlanningStepDraft(
                key="secure-work",
                title="Use secure capability",
                assignment=AgentAssignment(agent_id=agent_id, agent_revision=revision),
                capability_requirements=(
                    CapabilityRequirement(capability_id="capability.secure"),
                ),
            ),
        ),
    )


def _kernel() -> PlatformKernel:
    return PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=InMemoryKernelRepository(),
    )


async def _ready_task(kernel: PlatformKernel, key: str) -> str:
    created = await kernel.create_task(
        idempotency_key=f"{key}:create",
        title="Issue 439 server planning environment",
        objective="Resolve planning authority on the server",
        owner_type="user",
        owner_id=OWNER.id,
    )
    ready = await kernel.ready_task(
        idempotency_key=f"{key}:ready",
        task_id=created.task_id,
    )
    return ready.task_id


async def _capabilities() -> CapabilityRegistry:
    registry = CapabilityRegistry()
    await registry.register_provider(_SecureCapabilityProvider())
    return registry


def test_control_plane_uses_authenticated_server_environment() -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agents()
        kernel = _kernel()
        task_id = await _ready_task(kernel, "server-environment")
        resolver = _Resolver(
            PlanningEnvironment(
                granted_permissions=frozenset({"workspace.write"}),
                available_worker_capabilities=frozenset({"secure-worker"}),
                allowed_agent_ids=frozenset({agent_id}),
                allowed_capability_ids=frozenset({"capability.secure"}),
            )
        )
        planning = PlanningService(
            planner=DeterministicReferencePlanner(_draft(agent_id, revision)),
            repository=InMemoryPlanningRepository(),
            kernel=kernel,
            agents=agents,
            capabilities=await _capabilities(),
            environment_resolver=resolver,
        )
        handler = PlanningCommandHandlers(planning)
        context = RequestContext(
            request_id="request-439-server-environment",
            correlation_id="correlation-439-server-environment",
            actor=ActorContext(
                principal_ref="user:authenticated-planner",
                actor_type="user",
            ),
            idempotency_key="server-environment:propose",
        )

        resource = await handler.propose(context, task_id, {})

        assert resource["status"] == ProposalStatus.VALIDATED.value
        assert resolver.calls == [
            (
                "user:authenticated-planner",
                OWNER.id,
                None,
                PlanningTrigger.INITIAL,
            )
        ]

    asyncio.run(scenario())


def test_server_allowsets_remove_unauthorized_agent_from_planner_inventory() -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agents()
        kernel = _kernel()
        task_id = await _ready_task(kernel, "server-agent-filter")
        resolver = _Resolver(
            PlanningEnvironment(
                granted_permissions=frozenset({"workspace.write"}),
                available_worker_capabilities=frozenset({"secure-worker"}),
                allowed_agent_ids=frozenset(),
                allowed_capability_ids=frozenset({"capability.secure"}),
            )
        )
        planning = PlanningService(
            planner=DeterministicReferencePlanner(_draft(agent_id, revision)),
            repository=InMemoryPlanningRepository(),
            kernel=kernel,
            agents=agents,
            capabilities=await _capabilities(),
            environment_resolver=resolver,
        )

        proposal = await planning.propose(
            task_id=task_id,
            idempotency_key="server-agent-filter:propose",
        )

        assert proposal.status is ProposalStatus.INVALID
        assert any("references missing Agent revision" in error for error in proposal.validation.errors)

    asyncio.run(scenario())


def test_missing_server_resolver_does_not_grant_protected_capability() -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agents()
        kernel = _kernel()
        task_id = await _ready_task(kernel, "server-fail-closed")
        planning = PlanningService(
            planner=DeterministicReferencePlanner(_draft(agent_id, revision)),
            repository=InMemoryPlanningRepository(),
            kernel=kernel,
            agents=agents,
            capabilities=await _capabilities(),
        )

        proposal = await planning.propose(
            task_id=task_id,
            idempotency_key="server-fail-closed:propose",
        )

        assert proposal.status is ProposalStatus.INVALID
        assert any("requires missing capability capability.secure" in error for error in proposal.validation.errors)

    asyncio.run(scenario())


def test_callers_cannot_self_assert_planning_authority() -> None:
    async def scenario() -> None:
        agents, agent_id, revision = _agents()
        kernel = _kernel()
        task_id = await _ready_task(kernel, "server-authority-reject")
        resolver = _Resolver(
            PlanningEnvironment(
                granted_permissions=frozenset({"workspace.write"}),
                available_worker_capabilities=frozenset({"secure-worker"}),
            )
        )
        planning = PlanningService(
            planner=DeterministicReferencePlanner(_draft(agent_id, revision)),
            repository=InMemoryPlanningRepository(),
            kernel=kernel,
            agents=agents,
            capabilities=await _capabilities(),
            environment_resolver=resolver,
        )

        with pytest.raises(ContractError) as direct_exc:
            await planning.propose(
                task_id=task_id,
                idempotency_key="server-authority-reject:direct",
                granted_permissions=frozenset({"workspace.write"}),
            )
        assert direct_exc.value.code is ErrorCode.INVALID_REQUEST
        assert resolver.calls == []

        handler = PlanningCommandHandlers(planning)
        context = RequestContext(
            request_id="request-439-authority-reject",
            correlation_id="correlation-439-authority-reject",
            actor=ActorContext(principal_ref="user:planner", actor_type="user"),
            idempotency_key="server-authority-reject:control-plane",
        )
        with pytest.raises(ContractError) as command_exc:
            await handler.propose(
                context,
                task_id,
                {
                    "granted_permissions": ["workspace.write"],
                    "available_worker_capabilities": ["secure-worker"],
                },
            )
        assert command_exc.value.code is ErrorCode.INVALID_REQUEST
        assert resolver.calls == []

    asyncio.run(scenario())
