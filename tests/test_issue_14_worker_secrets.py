from __future__ import annotations

import asyncio

import pytest

from ai_multi_agent_platform.configuration import (
    LocalSecretProvider,
    SecretAccessContext,
    SecretMaterial,
)
from ai_multi_agent_platform.contracts import (
    ContractError,
    ExecutionHandle,
    ExecutionRequest,
    ExecutionSnapshot,
    OperationContext,
)
from ai_multi_agent_platform.distributed import JobRequirements, WorkerJobRequest
from ai_multi_agent_platform.distributed.registry import RegistryError
from ai_multi_agent_platform.distributed.secrets import (
    MappingWorkerSecretReferenceResolver,
    SecretDeliveringWorkerDispatcher,
    WorkerSecretBundle,
)
from ai_multi_agent_platform.domain import RunStatus, new_id
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    AuthorizationGate,
    LocalAuthorizationProvider,
    LocalPrincipalPolicy,
    ResourceType,
    SecretReference,
)
from ai_multi_agent_platform.security.enforced_providers import AuthorizedSecretProvider


class _CapturingSecretProvider(LocalSecretProvider):
    def __init__(self) -> None:
        super().__init__()
        self.contexts: list[SecretAccessContext] = []

    async def resolve(
        self,
        reference: SecretReference,
        context: SecretAccessContext,
    ) -> SecretMaterial:
        self.contexts.append(context)
        return await super().resolve(reference, context)


class _SecretAwareDispatcher:
    def __init__(self, worker_id: str) -> None:
        self._worker_id = worker_id
        self.dispatch_calls = 0
        self.expected_values: dict[str, str] = {}
        self.received_refs: list[tuple[str, ...]] = []
        self.bundle_reprs: list[str] = []
        self.jobs: dict[str, WorkerJobRequest] = {}
        self.status = RunStatus.RUNNING

    @property
    def worker_id(self) -> str:
        return self._worker_id

    async def dispatch_with_secrets(
        self,
        job: WorkerJobRequest,
        secrets: WorkerSecretBundle,
    ) -> ExecutionHandle:
        self.dispatch_calls += 1
        self.jobs[job.worker_job_id] = job
        self.received_refs.append(tuple(secret.secret_ref for secret in secrets.secrets))
        self.bundle_reprs.append(repr(secrets))
        for secret_ref, expected_value in self.expected_values.items():
            assert secrets.get(secret_ref).reveal() == expected_value
        return ExecutionHandle(run_id=job.execution.run_id)

    async def get(self, worker_job_id: str) -> ExecutionSnapshot:
        job = self.jobs[worker_job_id]
        return ExecutionSnapshot(run_id=job.execution.run_id, status=self.status)

    async def cancel(self, worker_job_id: str) -> ExecutionSnapshot:
        job = self.jobs[worker_job_id]
        self.status = RunStatus.CANCELLED
        return ExecutionSnapshot(run_id=job.execution.run_id, status=self.status)


def _reference(project_id: str, secret_id: str = "worker-api-token") -> SecretReference:
    return SecretReference(
        provider="local-secrets",
        secret_id=secret_id,
        scope=project_id,
    )


def _job(
    project_id: str,
    workspace_id: str,
    secret_refs: tuple[str, ...],
    *,
    timeout_seconds: float | None = 12.2,
) -> WorkerJobRequest:
    return WorkerJobRequest(
        execution=ExecutionRequest(
            run_id=new_id("run"),
            subject_type="task",
            subject_id=new_id("task"),
            context=OperationContext(
                correlation_id="issue-14-secret-delivery",
                project_id=project_id,
            ),
        ),
        requirements=JobRequirements(capability_refs=(new_id("cap"),)),
        workspace_ref=workspace_id,
        secret_refs=secret_refs,
        timeout_seconds=timeout_seconds,
    )


def test_worker_secret_delivery_uses_narrow_context_and_redacted_ephemeral_bundle() -> None:
    worker_id = new_id("worker")
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    portable_ref = "secret:worker-api-token"
    reference = _reference(project_id)
    provider = _CapturingSecretProvider()
    inner = _SecretAwareDispatcher(worker_id)
    inner.expected_values[portable_ref] = "TOP-SECRET-WORKER-VALUE"
    dispatcher = SecretDeliveringWorkerDispatcher(
        inner,
        provider,
        MappingWorkerSecretReferenceResolver({portable_ref: reference}),
    )
    job = _job(project_id, workspace_id, (portable_ref,))

    async def scenario() -> None:
        await provider.create(
            reference,
            "TOP-SECRET-WORKER-VALUE",
            purpose="worker_execution",
            allowed_consumers=(worker_id,),
            allowed_purposes=("worker_execution",),
        )
        handle = await dispatcher.dispatch(job)
        assert handle.run_id == job.execution.run_id

    asyncio.run(scenario())

    assert inner.dispatch_calls == 1
    assert inner.received_refs == [(portable_ref,)]
    assert "TOP-SECRET-WORKER-VALUE" not in inner.bundle_reprs[0]
    assert "[REDACTED]" in inner.bundle_reprs[0]
    assert job.secret_refs == (portable_ref,)
    assert len(provider.contexts) == 1
    access = provider.contexts[0]
    assert access.consumer_ref == worker_id
    assert access.project_id == project_id
    assert access.workspace_id == workspace_id
    assert access.task_id == job.execution.subject_id
    assert access.run_id == job.execution.run_id
    assert access.action == "worker.execute"
    assert access.capability_ref == job.requirements.capability_refs[0]
    assert access.purpose == "worker_execution"
    assert access.requested_lifetime_seconds == 13


def test_authorized_secret_provider_denial_prevents_worker_dispatch() -> None:
    worker_id = new_id("worker")
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    portable_ref = "secret:denied"
    reference = _reference(project_id, "denied")
    raw = LocalSecretProvider()
    authorization = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=worker_id,
                actor_types=frozenset({ActorType.WORKER}),
                allowed_actions=frozenset({AuthorizationAction.READ}),
                resource_types=frozenset({ResourceType.SECRET_REFERENCE}),
            ),
        )
    )
    provider = AuthorizedSecretProvider(raw, AuthorizationGate(authorization))
    inner = _SecretAwareDispatcher(worker_id)
    dispatcher = SecretDeliveringWorkerDispatcher(
        inner,
        provider,
        MappingWorkerSecretReferenceResolver({portable_ref: reference}),
    )
    job = _job(project_id, workspace_id, (portable_ref,))

    async def scenario() -> None:
        await raw.create(
            reference,
            "DENIED-WORKER-SECRET",
            purpose="worker_execution",
            allowed_consumers=(worker_id,),
            allowed_purposes=("worker_execution",),
        )
        with pytest.raises(ContractError):
            await dispatcher.dispatch(job)

    asyncio.run(scenario())
    assert inner.dispatch_calls == 0
    assert "DENIED-WORKER-SECRET" not in repr(authorization)


def test_authorized_secret_provider_allows_exact_worker_sensitive_capability() -> None:
    worker_id = new_id("worker")
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    portable_ref = "secret:allowed"
    reference = _reference(project_id, "allowed")
    raw = LocalSecretProvider()
    authorization = LocalAuthorizationProvider(
        (
            LocalPrincipalPolicy(
                principal_ref=worker_id,
                actor_types=frozenset({ActorType.WORKER}),
                allowed_actions=frozenset({AuthorizationAction.INVOKE_SENSITIVE_CAPABILITY}),
                resource_types=frozenset({ResourceType.SECRET_REFERENCE}),
            ),
        )
    )
    gate = AuthorizationGate(authorization)
    provider = AuthorizedSecretProvider(raw, gate)
    inner = _SecretAwareDispatcher(worker_id)
    inner.expected_values[portable_ref] = "AUTHORIZED-WORKER-SECRET"
    dispatcher = SecretDeliveringWorkerDispatcher(
        inner,
        provider,
        MappingWorkerSecretReferenceResolver({portable_ref: reference}),
    )
    job = _job(project_id, workspace_id, (portable_ref,))

    async def scenario() -> None:
        await raw.create(
            reference,
            "AUTHORIZED-WORKER-SECRET",
            purpose="worker_execution",
            allowed_consumers=(worker_id,),
            allowed_purposes=("worker_execution",),
        )
        await dispatcher.dispatch(job)

    asyncio.run(scenario())
    assert inner.dispatch_calls == 1
    assert "AUTHORIZED-WORKER-SECRET" not in repr(gate.audit_records)


def test_worker_secret_delivery_resolves_current_value_on_each_dispatch_attempt() -> None:
    worker_id = new_id("worker")
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    portable_ref = "secret:rotating"
    reference = _reference(project_id, "rotating")
    provider = _CapturingSecretProvider()
    inner = _SecretAwareDispatcher(worker_id)
    resolver = MappingWorkerSecretReferenceResolver({portable_ref: reference})
    dispatcher = SecretDeliveringWorkerDispatcher(inner, provider, resolver)
    job = _job(project_id, workspace_id, (portable_ref,), timeout_seconds=None)

    async def scenario() -> None:
        await provider.create(
            reference,
            "VERSION-ONE",
            purpose="worker_execution",
            allowed_consumers=(worker_id,),
            allowed_purposes=("worker_execution",),
        )
        inner.expected_values[portable_ref] = "VERSION-ONE"
        await dispatcher.dispatch(job)
        await provider.rotate(reference, "VERSION-TWO")
        inner.expected_values[portable_ref] = "VERSION-TWO"
        await dispatcher.dispatch(job)

    asyncio.run(scenario())
    assert inner.dispatch_calls == 2
    assert len(provider.contexts) == 2
    assert all(context.requested_lifetime_seconds == 300 for context in provider.contexts)
    assert "VERSION-ONE" not in repr(inner.bundle_reprs)
    assert "VERSION-TWO" not in repr(inner.bundle_reprs)


def test_unknown_secret_reference_fails_before_worker_receives_job() -> None:
    worker_id = new_id("worker")
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    inner = _SecretAwareDispatcher(worker_id)
    dispatcher = SecretDeliveringWorkerDispatcher(
        inner,
        LocalSecretProvider(),
        MappingWorkerSecretReferenceResolver({}),
    )
    job = _job(project_id, workspace_id, ("secret:missing",))

    async def scenario() -> None:
        with pytest.raises(RegistryError, match="unknown Worker secret reference"):
            await dispatcher.dispatch(job)

    asyncio.run(scenario())
    assert inner.dispatch_calls == 0


def test_job_without_secret_refs_does_not_touch_unavailable_secret_backend() -> None:
    worker_id = new_id("worker")
    project_id = new_id("project")
    workspace_id = new_id("workspace")
    inner = _SecretAwareDispatcher(worker_id)
    dispatcher = SecretDeliveringWorkerDispatcher(
        inner,
        LocalSecretProvider(available=False),
        MappingWorkerSecretReferenceResolver({}),
    )
    job = _job(project_id, workspace_id, ())

    async def scenario() -> None:
        handle = await dispatcher.dispatch(job)
        assert handle.run_id == job.execution.run_id

    asyncio.run(scenario())
    assert inner.dispatch_calls == 1
    assert inner.received_refs == [()]
