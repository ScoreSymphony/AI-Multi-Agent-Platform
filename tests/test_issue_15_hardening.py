from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ai_multi_agent_platform.control_plane import ActorContext, ControlPlane, RequestContext
from ai_multi_agent_platform.contracts import ContractError, ErrorCode, OperationContext
from ai_multi_agent_platform.data import (
    DataAccessContext,
    KnowledgeSource,
    KnowledgeStatus,
    LocalFileProvider,
    LocalKnowledgeProvider,
    LocalMemoryProvider,
    MemoryEntry,
    MemoryQuery,
    MemoryScope,
    RetentionPolicy,
)
from ai_multi_agent_platform.domain import Approval, TaskStatus, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    AuthorizedDataFileProvider,
    AuthorizedDataKnowledgeProvider,
    AuthorizedDataMemoryProvider,
    ControlPlaneAuthorizationBridge,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
    canonical_control_plane_vocabulary,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator


def _data_context(*, project_id: str | None = None) -> DataAccessContext:
    return DataAccessContext(
        operation=OperationContext(
            correlation_id="corr-data-hardening",
            owner_type="user",
            owner_id="alice",
            project_id=project_id,
        ),
        actor_ref="user:alice",
    )


def test_refined_issue_13_data_paths_cannot_bypass_authorization(tmp_path) -> None:
    project_id = new_id("project")
    task_id = new_id("task")
    context = _data_context(project_id=project_id)
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref="user:alice",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.READ}),
                resource_types=frozenset(
                    {
                        ResourceType.FILE,
                        ResourceType.MEMORY,
                        ResourceType.KNOWLEDGE_SOURCE,
                    }
                ),
                project_ids=frozenset({project_id}),
            ),
        )
    )
    gate = AuthorizationGate(provider)

    raw_files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite")
    files = AuthorizedDataFileProvider(raw_files, gate)
    with pytest.raises(ContractError) as file_error:
        asyncio.run(files.create_file(b"blocked", context))
    assert file_error.value.code is ErrorCode.FORBIDDEN
    assert asyncio.run(raw_files.list_files(context)) == ()

    raw_memory = LocalMemoryProvider(tmp_path / "memory.sqlite")
    memory = AuthorizedDataMemoryProvider(raw_memory, gate)
    entry = MemoryEntry(
        memory_id=new_id("memory"),
        scope=MemoryScope.TASK,
        scope_id=task_id,
        owner_ref="user:alice",
        created_by="user:alice",
        value={"blocked": True},
        created_at=datetime.now(UTC),
        retention=RetentionPolicy.TASK_LIFETIME,
    )
    with pytest.raises(ContractError) as memory_error:
        asyncio.run(memory.write_entry(entry, context))
    assert memory_error.value.code is ErrorCode.FORBIDDEN
    assert asyncio.run(
        raw_memory.query_entries(MemoryQuery(scope=MemoryScope.TASK, scope_id=task_id), context)
    ) == ()

    raw_knowledge = LocalKnowledgeProvider(tmp_path / "knowledge.sqlite")
    knowledge = AuthorizedDataKnowledgeProvider(raw_knowledge, gate)
    now = datetime.now(UTC)
    source = KnowledgeSource(
        source_id=new_id("knowledge_source"),
        project_id=project_id,
        owner_ref="user:alice",
        created_by="user:alice",
        title="Blocked source",
        revision="1",
        status=KnowledgeStatus.REGISTERED,
        created_at=now,
        updated_at=now,
    )
    with pytest.raises(ContractError) as knowledge_error:
        asyncio.run(knowledge.register_source(source, context))
    assert knowledge_error.value.code is ErrorCode.FORBIDDEN
    with pytest.raises(ContractError) as raw_lookup:
        asyncio.run(raw_knowledge.get_index_status(source.source_id, context))
    assert raw_lookup.value.code is ErrorCode.NOT_FOUND


def test_control_plane_bridge_uses_canonical_vocabulary_and_resumes_after_approval() -> None:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    provider = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref="user:test",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.CREATE}),
                approval_actions=frozenset({AuthorizationAction.EXECUTE}),
                resource_types=frozenset({ResourceType.TASK}),
            ),
            LocalPrincipalPolicy(
                principal_ref="user:reviewer",
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.APPROVE}),
                resource_types=frozenset({ResourceType.TASK}),
            ),
        )
    )
    gate = AuthorizationGate(provider)
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=ControlPlaneAuthorizationBridge(gate),
    )
    actor = ActorContext(
        principal_ref="user:test",
        owner_type="user",
        owner_id="test",
    )
    create_context = RequestContext(
        request_id="request-create",
        correlation_id="corr-create",
        actor=actor,
        idempotency_key="create-task-hardening",
    )
    task = asyncio.run(
        control_plane.create_task(
            create_context,
            {
                "title": "Approval boundary",
                "objective": "Verify canonical control-plane authorization",
                "owner_type": "user",
                "owner_id": "test",
            },
        )
    )
    task_id = task["id"]
    assert isinstance(task_id, str)

    queue_context = RequestContext(
        request_id="request-queue",
        correlation_id="corr-queue",
        actor=actor,
        idempotency_key="queue-task-hardening",
    )
    with pytest.raises(ContractError) as pending_error:
        asyncio.run(control_plane.queue_task(queue_context, task_id))
    assert pending_error.value.code is ErrorCode.FORBIDDEN
    pending = gate.approvals.all()
    assert len(pending) == 1
    assert isinstance(pending[0].approval, Approval)

    asyncio.run(
        gate.decide_approval(
            pending[0].approval_id,
            approver=ActorIdentity("user:reviewer", ActorType.HUMAN),
            approve=True,
            operation=OperationContext(
                correlation_id="corr-review-control-plane",
                owner_type="user",
                owner_id="reviewer",
            ),
        )
    )
    queued = asyncio.run(control_plane.queue_task(queue_context, task_id))
    assert queued["status"] == TaskStatus.READY.value

    decision_records = [record for record in gate.audit_records if record.actor_ref == "user:test"]
    assert decision_records[-1].action is AuthorizationAction.EXECUTE
    assert decision_records[-1].resource_type is ResourceType.TASK


def test_control_plane_vocabulary_mapping_is_platform_owned() -> None:
    assert canonical_control_plane_vocabulary("project:create") == (
        AuthorizationAction.CREATE,
        ResourceType.PROJECT,
    )
    assert canonical_control_plane_vocabulary("model-provider:disable") == (
        AuthorizationAction.ADMINISTER,
        ResourceType.PROVIDER_CONFIGURATION,
    )
