from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, cast

import pytest

from ai_multi_agent_platform.agents import (
    STANDARD_AGENT_IDS,
    AgentRuntime,
    AgentService,
    InMemoryAgentRepository,
    bootstrap_standard_agents,
)
from ai_multi_agent_platform.context import (
    OperationalContextSourceRequest,
    VerificationContextSourceAdapter,
)
from ai_multi_agent_platform.contracts import ContractError, OperationContext
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.planning.evidence import ReplanningEvidenceBridge
from ai_multi_agent_platform.planning.service import PlanningService
from ai_multi_agent_platform.verification import (
    SqliteVerificationService,
    VerificationAuditEvent,
    VerificationCompletionAuthority,
    VerificationOutcome,
    VerificationPolicy,
    VerificationRequest,
    VerificationResult,
    VerificationStage,
    VerificationSubject,
    VerifierIdentity,
    VerifierKind,
    runtime_verification_service,
)
from ai_multi_agent_platform.verification.async_agent_workflow import AsyncAutomaticReviewerWorkflow
from ai_multi_agent_platform.verification.observability import VerificationTimelineReader
from ai_multi_agent_platform.verification.output_workflow import PolicyMetadataReviewerResolver
from ai_multi_agent_platform.verification.reviewer_recovery import (
    AutomaticReviewerStartupReconciler,
)


class _SlowSqliteVerificationService(SqliteVerificationService):
    def __init__(self, path: Path) -> None:
        self.delay_seconds = 0.0
        self.method_delay_seconds = 0.0
        self.connection_threads: list[str] = []
        self.history_threads: list[str] = []
        self.policy_threads: list[str] = []
        self.audit_threads: list[str] = []
        super().__init__(path)

    def _connect(self) -> sqlite3.Connection:
        self.connection_threads.append(threading.current_thread().name)
        if self.delay_seconds:
            time.sleep(self.delay_seconds)
        return super()._connect()

    def history(
        self,
        *,
        task_id: str,
    ) -> tuple[tuple[VerificationRequest, VerificationResult | None], ...]:
        self.history_threads.append(threading.current_thread().name)
        if self.method_delay_seconds:
            time.sleep(self.method_delay_seconds)
        return super().history(task_id=task_id)

    def get_policy(self, policy_id: str, version: int) -> VerificationPolicy:
        self.policy_threads.append(threading.current_thread().name)
        if self.method_delay_seconds:
            time.sleep(self.method_delay_seconds)
        return super().get_policy(policy_id, version)

    def audit_history(
        self,
        *,
        task_id: str | None = None,
        verification_id: str | None = None,
    ) -> tuple[VerificationAuditEvent, ...]:
        self.audit_threads.append(threading.current_thread().name)
        if self.method_delay_seconds:
            time.sleep(self.method_delay_seconds)
        return super().audit_history(task_id=task_id, verification_id=verification_id)


def _completed_verification(
    service: SqliteVerificationService,
) -> tuple[str, str]:
    task_id = new_id("task")
    project_id = new_id("project")
    subject = VerificationSubject(
        subject_type="result",
        subject_id=new_id("result"),
        revision="1",
        digest="a" * 64,
    )
    policy = service.register_policy(
        VerificationPolicy(
            name="async consumer regression",
            stages=(VerificationStage("review", VerifierKind.DETERMINISTIC),),
        )
    )
    request = service.request_verification(
        task_id=task_id,
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=subject,
        correlation_id=task_id,
        result_id=subject.subject_id,
        project_id=project_id,
    )
    service.submit_result(
        VerificationResult(
            verification_id=request.verification_id,
            verifier=VerifierIdentity(
                verifier_ref="deterministic:async-consumer",
                kind=VerifierKind.DETERMINISTIC,
                read_only=True,
            ),
            outcome=VerificationOutcome.PASS,
            subject=subject,
            checks_executed=("async_consumer_regression",),
        )
    )
    return task_id, project_id


def _automatic_reviewer_request(
    service: _SlowSqliteVerificationService,
) -> tuple[VerificationCompletionAuthority, VerificationRequest]:
    reviewer_id = STANDARD_AGENT_IDS["reviewer"]
    policy = service.register_policy(
        VerificationPolicy(
            name="async automatic reviewer regression",
            stages=(VerificationStage("review", VerifierKind.AGENT),),
            metadata={
                "automatic_reviewer": {
                    "enabled": True,
                    "subject_types": ["result"],
                    "stages": {
                        "review": {
                            "agent_id": reviewer_id,
                            "agent_revision": 1,
                        }
                    },
                }
            },
        )
    )
    result_id = new_id("result")
    request = service.request_verification(
        task_id=new_id("task"),
        policy_id=policy.policy_id,
        policy_version=policy.version,
        stage_id="review",
        subject=VerificationSubject(
            subject_type="result",
            subject_id=result_id,
            revision="1",
            digest="b" * 64,
        ),
        correlation_id="issue-892-async-reviewer",
        run_id=new_id("run"),
        result_id=result_id,
    )
    return VerificationCompletionAuthority(service), request


def _agents() -> AgentRuntime:
    service = AgentService(InMemoryAgentRepository())
    bootstrap_standard_agents(service)
    return AgentRuntime(service)


def _source_request(task_id: str, project_id: str) -> OperationalContextSourceRequest:
    return OperationalContextSourceRequest(
        task_id=task_id,
        run_id=new_id("run"),
        agent_id=new_id("agent"),
        agent_revision=1,
        project_id=project_id,
        workspace_id=None,
        operation=OperationContext(
            correlation_id=task_id,
            owner_type="user",
            owner_id="issue-892",
            project_id=project_id,
        ),
        actor_ref="user:issue-892",
    )


async def _heartbeat_until(task: asyncio.Task[object]) -> int:
    heartbeat = 0
    while not task.done():
        heartbeat += 1
        await asyncio.sleep(0.005)
    return heartbeat


def test_context_verification_reads_stay_off_event_loop(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _SlowSqliteVerificationService(tmp_path / "verification.sqlite3")
        task_id, project_id = _completed_verification(service)
        service.history_threads.clear()
        service.policy_threads.clear()
        service.method_delay_seconds = 0.08
        source = VerificationContextSourceAdapter(service)

        projection = asyncio.create_task(source.collect(_source_request(task_id, project_id)))
        heartbeat = await _heartbeat_until(projection)
        candidates = await projection

        assert len(candidates) == 1
        assert heartbeat >= 2
        assert service.history_threads
        assert service.policy_threads
        assert all(name.startswith("verification-persistence") for name in service.history_threads)
        assert all(name.startswith("verification-persistence") for name in service.policy_threads)

    asyncio.run(scenario())


def test_verification_timeline_reads_stay_off_event_loop(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _SlowSqliteVerificationService(tmp_path / "verification.sqlite3")
        task_id, _project_id = _completed_verification(service)
        service.audit_threads.clear()
        service.method_delay_seconds = 0.08
        reader = VerificationTimelineReader(service)

        projection = asyncio.create_task(reader.query_timeline_async(task_id=task_id))
        heartbeat = await _heartbeat_until(projection)
        entries = await projection

        assert entries
        assert heartbeat >= 2
        assert service.audit_threads
        assert all(name.startswith("verification-persistence") for name in service.audit_threads)

    asyncio.run(scenario())


def test_policy_metadata_reviewer_resolution_uses_verification_offload(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _SlowSqliteVerificationService(tmp_path / "verification.sqlite3")
        completion, request = _automatic_reviewer_request(service)
        agents = _agents()
        workflow = cast(Any, object.__new__(AsyncAutomaticReviewerWorkflow))
        workflow._resolver = PolicyMetadataReviewerResolver(completion)
        workflow._runtime_verification = runtime_verification_service(service)
        workflow._agents = agents
        service.policy_threads.clear()
        service.method_delay_seconds = 0.08

        resolution = asyncio.create_task(workflow._resolve_reviewer_assignment(request))
        heartbeat = await _heartbeat_until(resolution)
        selected = await resolution

        assert selected.agent_id == STANDARD_AGENT_IDS["reviewer"]
        assert heartbeat >= 2
        assert service.policy_threads
        assert all(name.startswith("verification-persistence") for name in service.policy_threads)

    asyncio.run(scenario())


def test_reviewer_recovery_policy_binding_uses_verification_offload(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _SlowSqliteVerificationService(tmp_path / "verification.sqlite3")
        _completion, request = _automatic_reviewer_request(service)
        recovery = AutomaticReviewerStartupReconciler(
            workflow=cast(Any, object()),
            agents=_agents(),
            verification=service,
        )
        service.policy_threads.clear()
        service.method_delay_seconds = 0.08

        check = asyncio.create_task(recovery._review_binding_conflict(request))
        heartbeat = await _heartbeat_until(check)
        conflict = await check

        assert conflict is None
        assert heartbeat >= 2
        assert service.policy_threads
        assert all(name.startswith("verification-persistence") for name in service.policy_threads)

    asyncio.run(scenario())


def test_planning_verification_evidence_uses_verification_offload(tmp_path: Path) -> None:
    async def scenario() -> None:
        service = _SlowSqliteVerificationService(tmp_path / "verification.sqlite3")
        task_id, _project_id = _completed_verification(service)
        event = next(
            event
            for event in service.audit_history(task_id=task_id)
            if event.outcome is VerificationOutcome.PASS
        )
        bridge = ReplanningEvidenceBridge(
            cast(PlanningService, object()),
            verification_repository=service,
        )
        service.audit_threads.clear()
        service.method_delay_seconds = 0.08

        replanning = asyncio.create_task(bridge.from_verification(event))
        heartbeat = await _heartbeat_until(replanning)
        with pytest.raises(ContractError):
            await replanning

        assert heartbeat >= 2
        assert service.audit_threads
        assert all(name.startswith("verification-persistence") for name in service.audit_threads)

    asyncio.run(scenario())
