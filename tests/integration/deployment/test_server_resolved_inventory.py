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
    ContractError,
    ErrorCode,
    HealthStatus,
    OperationContext,
)
from ai_multi_agent_platform.contracts.types import (
    Capability,
    CapabilityKind,
    ProviderDescriptor,
    ToolInvocation,
    ToolResult,
)
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.kernel.models import TaskState
from ai_multi_agent_platform.planning import (
    DeterministicReferencePlanner,
    InMemoryPlanningRepository,
    PlannerOutput,
    PlanningEnvironment,
    PlanningOrchestratorAdapter,
    PlanningRequest,
    ProposalStatus,
)
from ai_multi_agent_platform.planning.composition import ReferencePlanningService
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

OWNER = OwnerRef(type="user", id="issue-439-inventory")
SECURE_CAPABILITY_ID = "tool.issue-439-secure"


def _profile(name: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role="worker",
        instructions=AgentInstructions(
            role=InstructionSource(content="Execute one bounded planning Step.", version="1")
        ),
    )


def _agents() -> tuple[InMemoryAgentRepository, tuple[str, int], tuple[str, int]]:
    repository = InMemoryAgentRepository()
    service = AgentService(repository)
    allowed = service.create_agent(_profile("Allowed worker"), owner_ref=OWNER)
    denied = service.create_agent(_profile("Denied worker"), owner_ref=OWNER)
    return (
        repository,
        (allowed.agent_id, allowed.revision),
        (denied.agent_id, denied.revision),
    )


def _kernel(repository: InMemoryPlanningRepository) -> PlatformKernel:
    return PlatformKernel(
        orchestrator=PlanningOrchestratorAdapter(repository, fallback=FakeOrchestrator()),
        lifecycle=FakeLifecycleBackend(),
        repository=InMemoryKernelRepository(),
    )


async def _ready_task(kernel: PlatformKernel, key: str) -> str:
    task = await kernel.create_task(
        idempotency_key=f"{key}:create",
        title="Server-resolved planning inventory",
        objective="Use only trusted planning candidates",
        owner_type="user",
        owner_id=OWNER.id,
    )
    ready = await kernel.ready_task(
        idempotency_key=f"{key}:ready",
        task_id=task.task_id,
    )
    return ready.task_id


class FixedEnvironmentResolver:
    def __init__(self, environment: PlanningEnvironment) -> None:
        self.environment = environment

    async def resolve(
        self,
        *,
        task: TaskState,
        context: OperationContext,
        workspace_id: str | None,
    ) -> PlanningEnvironment:
        del task, context, workspace_id
        return self.environment


class RecordingPlanner(DeterministicReferencePlanner):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[PlanningRequest] = []

    async def propose(self, request: PlanningRequest) -> PlannerOutput:
        self.requests.append(request)
        return await super().propose(request)


class SecureCapabilityProvider(CapabilityToolProvider):
    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id="issue-439-secure-provider",
            provider_type="test",
            capabilities=(
                Capability(
                    name=SECURE_CAPABILITY_ID,
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
                    capability_id=SECURE_CAPABILITY_ID,
                    name="Issue 439 secure capability",
                    version="1.0",
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    required_permissions=("workspace.write",),
                    required_worker_capabilities=("secure-worker",),
                    health=HealthStatus.HEALTHY,
                ),
                provider_id=self.descriptor.provider_id,
                provider_tool_ref="issue-439.secure",
            ),
        )

    async def invoke(self, invocation: ToolInvocation) -> ToolResult:
        return ToolResult(invocation_id=invocation.invocation_id, output={"ok": True})


def test_reference_planning_uses_only_server_authorized_agent_revisions() -> None:
    async def scenario() -> None:
        agents, allowed, denied = _agents()
        repository = InMemoryPlanningRepository()
        kernel = _kernel(repository)
        planner = RecordingPlanner()
        resolver = FixedEnvironmentResolver(
            PlanningEnvironment(authorized_agent_revisions=frozenset({allowed}))
        )
        planning = ReferencePlanningService(
            planner=planner,
            repository=repository,
            kernel=kernel,
            agents=agents,
            environment_resolver=resolver,
        )
        task_id = await _ready_task(kernel, "authorized-agent")

        proposal = await planning.propose(
            task_id=task_id,
            idempotency_key="authorized-agent:propose",
        )
        assert proposal.status is ProposalStatus.VALIDATED
        assert len(planner.requests) == 1
        inventory = planner.requests[0].inventory
        assert {(item.agent_id, item.revision) for item in inventory.agents} == {allowed}
        assert denied not in {(item.agent_id, item.revision) for item in inventory.agents}

        with pytest.raises(ContractError) as exc_info:
            await planning.propose(
                task_id=task_id,
                idempotency_key="authorized-agent:caller-grant",
                granted_permissions=frozenset({"workspace.write"}),
            )
        assert exc_info.value.code is ErrorCode.INVALID_REQUEST
        assert "server-resolved" in exc_info.value.message

    asyncio.run(scenario())


def test_capability_inventory_requires_server_permissions_and_worker_availability() -> None:
    async def scenario() -> None:
        agents, allowed, _denied = _agents()
        capabilities = CapabilityRegistry()
        await capabilities.register_provider(SecureCapabilityProvider())
        repository = InMemoryPlanningRepository()
        kernel = _kernel(repository)
        planner = RecordingPlanner()
        resolver = FixedEnvironmentResolver(
            PlanningEnvironment(
                authorized_agent_revisions=frozenset({allowed}),
                authorized_capability_versions=frozenset({(SECURE_CAPABILITY_ID, "1.0")}),
            )
        )
        planning = ReferencePlanningService(
            planner=planner,
            repository=repository,
            kernel=kernel,
            agents=agents,
            capabilities=capabilities,
            environment_resolver=resolver,
        )

        task_id = await _ready_task(kernel, "capability-no-grants")
        await planning.propose(task_id=task_id, idempotency_key="capability-no-grants:propose")
        assert planner.requests[-1].inventory.capabilities == ()

        resolver.environment = PlanningEnvironment(
            granted_permissions=frozenset({"workspace.write"}),
            authorized_agent_revisions=frozenset({allowed}),
            authorized_capability_versions=frozenset({(SECURE_CAPABILITY_ID, "1.0")}),
        )
        task_id = await _ready_task(kernel, "capability-no-worker")
        await planning.propose(task_id=task_id, idempotency_key="capability-no-worker:propose")
        assert planner.requests[-1].inventory.capabilities == ()

        resolver.environment = PlanningEnvironment(
            granted_permissions=frozenset({"workspace.write"}),
            available_worker_capabilities=frozenset({"secure-worker"}),
            authorized_agent_revisions=frozenset({allowed}),
            authorized_capability_versions=frozenset({(SECURE_CAPABILITY_ID, "1.0")}),
        )
        task_id = await _ready_task(kernel, "capability-usable")
        await planning.propose(task_id=task_id, idempotency_key="capability-usable:propose")
        assert {
            (item.capability_id, item.version)
            for item in planner.requests[-1].inventory.capabilities
        } == {(SECURE_CAPABILITY_ID, "1.0")}

    asyncio.run(scenario())
