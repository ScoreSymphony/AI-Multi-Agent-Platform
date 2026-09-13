from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path

from ai_multi_agent_platform.adapters.single_node_app import build_default_single_node_deployment
from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.deployment import SingleNodeConfig, load_advanced_deployment_profile
from ai_multi_agent_platform.deployment.distributed_control_plane import (
    DeploymentWorkerProtocolService,
    platform_workspace_context,
)
from ai_multi_agent_platform.distributed import (
    LocalWorker,
    RegistrationRequest,
    WorkerRequestCredentials,
    WorkerTransportEndpoint,
)
from ai_multi_agent_platform.domain import RunStatus, TaskStatus
from ai_multi_agent_platform.execution import ExecutorLifecycleBackend, ReferenceExecutor
from ai_multi_agent_platform.messaging import InProcessMessageTransport
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    CredentialScope,
    LocalPrincipalPolicy,
    ResourceType,
)

PROFILES = Path("deploy/distributed/profiles")


def test_shipped_profiles_are_runnable_by_worker_processes() -> None:
    for name in (
        "multi-local-workers.json",
        "remote-worker.json",
        "cpu-control-gpu-worker.json",
        "heterogeneous-three-node.json",
    ):
        profile = load_advanced_deployment_profile(PROFILES / name)
        for node in profile.nodes:
            assert node.reporter_worker_id is not None
            assert node.reporter_worker_id in {worker.worker_id for worker in node.workers}
            assert node.binding.credential_reference is not None
            if node.binding.connection_mode == "local":
                assert "in-process" not in node.binding.transport_endpoint_ref


def test_http_task_run_executes_through_authenticated_distributed_worker(tmp_path: Path) -> None:
    config = SingleNodeConfig(
        data_dir=tmp_path / "control-plane",
        secure_cookie=False,
    )
    deployment = build_default_single_node_deployment(
        config,
        enable_distributed_execution=True,
    )
    runtime = deployment.distributed_runtime
    assert runtime is not None

    admin = deployment.bootstrap_admin(
        "issue240-admin",
        "issue-240-test-password-with-sufficient-length",
    )
    api_token = deployment.authentication.create_personal_access_token(
        admin.user_id,
        purpose="issue-240-distributed-task-e2e",
    )

    node = load_advanced_deployment_profile(PROFILES / "remote-worker.json").nodes[0]
    reporter_id = node.reporter_worker_id
    assert reporter_id is not None
    worker_id = node.workers[0].worker_id
    actions = frozenset(
        {
            AuthorizationAction.CREATE,
            AuthorizationAction.MODIFY,
            AuthorizationAction.DELETE,
        }
    )
    resource_types = frozenset({ResourceType.NODE, ResourceType.WORKER})
    worker_token = deployment.authentication.create_worker_credential(
        reporter_id,
        scope=CredentialScope(actions=actions, resource_types=resource_types),
    )
    deployment.authorization.register(
        LocalPrincipalPolicy(
            principal_ref=reporter_id,
            actor_types=frozenset({ActorType.WORKER}),
            allowed_actions=actions,
            resource_types=resource_types,
        )
    )

    transport = InProcessMessageTransport(provider_id="issue-240-canonical-task-e2e")
    service = DeploymentWorkerProtocolService(
        runtime,
        authentication=deployment.authentication,
        authorization=deployment.authorization,
        transport=transport,
        workspaces=deployment.workspaces,
        files=deployment.files,
        context_resolver=platform_workspace_context,
    )
    worker_root = tmp_path / "remote-worker"
    executor = ReferenceExecutor(worker_root)
    ExecutorLifecycleBackend.ensure_workspace(worker_root, "reference")
    endpoint = WorkerTransportEndpoint(
        LocalWorker(
            worker_id,
            ExecutorLifecycleBackend(executor, workspace="reference"),
        ),
        transport,
    )

    async def scenario() -> None:
        endpoint_task = asyncio.create_task(endpoint.serve())
        await asyncio.sleep(0)
        now = datetime.now(UTC)
        try:
            await service.register(
                RegistrationRequest(
                    node=node.node,
                    workers=node.workers,
                    service_identity_ref=reporter_id,
                ),
                WorkerRequestCredentials(
                    token=worker_token.secret,
                    nonce="issue-240-api-task-register",
                    issued_at=now,
                    tls_peer_ref="spiffe://issue-240/remote-worker",
                    request_id="issue-240-api-task-register",
                    correlation_id="issue-240-api-task-register",
                ),
                now=now,
            )

            headers = {
                "authorization": f"Bearer {api_token.secret}",
                "content-type": "application/json",
            }
            created = await deployment.http.handle(
                HTTPRequest(
                    method="POST",
                    path="/api/v1/tasks",
                    headers={**headers, "idempotency-key": "issue-240-task-create"},
                    body={
                        "title": "Distributed canonical Task",
                        "objective": "Execute through the normal Task/Run API on a remote Worker",
                        "owner_type": "user",
                        "owner_id": admin.user_id,
                    },
                )
            )
            assert created.status == 201, created.body
            assert isinstance(created.body, dict)
            task_id = created.body["id"]
            assert isinstance(task_id, str)

            queued = await deployment.http.handle(
                HTTPRequest(
                    method="POST",
                    path=f"/api/v1/tasks/{task_id}:queue",
                    headers={**headers, "idempotency-key": "issue-240-task-queue"},
                )
            )
            assert queued.status == 200, queued.body

            started = await deployment.http.handle(
                HTTPRequest(
                    method="POST",
                    path=f"/api/v1/tasks/{task_id}:start",
                    headers={**headers, "idempotency-key": "issue-240-task-start"},
                )
            )
            assert started.status == 200, started.body
            assert isinstance(started.body, dict)
            run_id = started.body["id"]
            assert isinstance(run_id, str)

            refreshed = await deployment.kernel.refresh_run(
                idempotency_key="issue-240-task-refresh",
                task_id=task_id,
                run_id=run_id,
            )
            task = await deployment.kernel.get_task(task_id)
            assert refreshed.status is RunStatus.SUCCEEDED
            assert task.status is TaskStatus.SUCCEEDED

            records = runtime.records()
            assert len(records) == 1
            assert records[0].worker_id == worker_id
            assert records[0].job.execution.run_id == run_id
            assert records[0].job.execution.subject_id == task_id
        finally:
            endpoint_task.cancel()
            with suppress(asyncio.CancelledError):
                await endpoint_task
            await transport.close(graceful=False)

    asyncio.run(scenario())
