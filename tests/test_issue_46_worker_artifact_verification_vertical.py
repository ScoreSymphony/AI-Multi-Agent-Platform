from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Mapping
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from ai_multi_agent_platform.adapters.onboarding_openai_compatible import (
    OpenAICompatibleOnboardingAdapter,
)
from ai_multi_agent_platform.agents import (
    AgentCapabilityPolicy,
    AgentInstructions,
    AgentModelPolicy,
    AgentProfile,
    AgentRunStatus,
    CapabilityConstraint,
    InstructionSource,
)
from ai_multi_agent_platform.agents.execution_profile import (
    AgentExecutionBinding,
    encode_agent_execution_binding,
)
from ai_multi_agent_platform.contracts import JsonValue, OperationContext
from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.data import DataAccessContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.distributed import (
    WORKSPACE_ARTIFACT_CAPABILITY_ID,
    ArtifactPublishingWorkerDispatcher,
    CanonicalWorkspaceArtifactPublisher,
    DistributedExecutorArtifactProvider,
    DistributedRegistry,
    DistributedRuntime,
    ExecutorWorker,
    MaterializingWorkerDispatcher,
    NodeRecord,
    RegistrationRequest,
    WorkerJobRequest,
    WorkerRecord,
    WorkspaceJobMaterializationResolver,
    tool_lineage,
)
from ai_multi_agent_platform.distributed.workspace_transport import (
    TransportRemoteWorkspaceMaterializer,
    WorkerWorkspaceMaterializationStore,
    WorkerWorkspaceTransportEndpoint,
)
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, TaskStatus, new_id, validate_id
from ai_multi_agent_platform.execution import ReferenceExecutor
from ai_multi_agent_platform.messaging import InProcessMessageTransport
from ai_multi_agent_platform.models import RoutingRequirements
from ai_multi_agent_platform.onboarding import FIRST_RUN_RESOURCE_ID
from ai_multi_agent_platform.verification import (
    VerificationOutcome,
    VerificationPolicy,
    VerificationStage,
    VerifierKind,
)
from ai_multi_agent_platform.workspaces import Workspace, WorkspaceChangeKind

_PASSWORD = "correct horse battery staple"
_ARTIFACT_PATH = "outputs/worker-verified-evidence.txt"
_ARTIFACT_CONTENT = "Worker-produced canonical evidence for Verification.\n"


class _ArtifactToolCallingModelHandler(BaseHTTPRequestHandler):
    chat_payloads: list[dict[str, object]] = []

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/v1/models":
            self.send_error(404)
            return
        self._json(200, {"data": [{"id": "local-worker-artifact-model"}]})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("content-length", "0"))
        parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        assert isinstance(parsed, dict)
        payload = dict(parsed)
        type(self).chat_payloads.append(payload)

        tools = payload.get("tools")
        assert payload["model"] == "local-worker-artifact-model"
        assert isinstance(tools, list) and len(tools) == 1
        tool = tools[0]
        assert isinstance(tool, dict)
        function = tool.get("function")
        assert isinstance(function, dict)
        tool_name = function.get("name")
        parameters = function.get("parameters")
        assert isinstance(tool_name, str)
        assert isinstance(parameters, dict)
        assert parameters.get("required") == ["path", "content"]

        self._json(
            200,
            {
                "model": "local-worker-artifact-model",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-worker-artifact",
                                    "type": "function",
                                    "function": {
                                        "name": tool_name,
                                        "arguments": json.dumps(
                                            {
                                                "path": _ARTIFACT_PATH,
                                                "content": _ARTIFACT_CONTENT,
                                            }
                                        ),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 13,
                    "completion_tokens": 7,
                    "total_tokens": 20,
                },
            },
        )

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _json(self, status: int, payload: object) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def _headers(token: str, *, key: str | None = None) -> dict[str, str]:
    headers = {
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
    }
    if key is not None:
        headers["idempotency-key"] = key
    return headers


def _model_payload(base_url: str) -> dict[str, JsonValue]:
    return {
        "resource_ref": FIRST_RUN_RESOURCE_ID,
        "adapter_id": "openai-compatible",
        "provider_id": "local-worker-artifact-provider",
        "model_config_id": "model-worker-artifact-vertical",
        "provider_model": "local-worker-artifact-model",
        "display_name": "Worker Artifact Vertical Model",
        "base_url": base_url,
        "location": "local",
        "capabilities": {
            "context_window": 32768,
            "tool_calling": True,
            "structured_output": False,
            "streaming": False,
            "modalities": ["text"],
        },
    }


def _agent_profile() -> AgentProfile:
    return AgentProfile(
        name="Worker Artifact Verification Agent",
        role="worker-artifact-verification-agent",
        instructions=AgentInstructions(
            role=InstructionSource(
                content="Use the supplied canonical capability to produce the requested evidence."
            )
        ),
        model=AgentModelPolicy(
            requirements=RoutingRequirements(modalities=("text",), tool_calling=True),
            allow_task_override=True,
        ),
        capabilities=AgentCapabilityPolicy(
            allowed=(WORKSPACE_ARTIFACT_CAPABILITY_ID,),
            constraints=(
                CapabilityConstraint(
                    capability_id=WORKSPACE_ARTIFACT_CAPABILITY_ID,
                    required=True,
                    exact_version="1.0",
                ),
            ),
        ),
    )


def test_authenticated_worker_artifact_is_exact_verification_evidence_same_run(
    tmp_path: Path,
) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ArtifactToolCallingModelHandler)
    _ArtifactToolCallingModelHandler.chat_payloads.clear()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def scenario() -> None:
        host, port = server.server_address
        distributed = DistributedRuntime(DistributedRegistry())
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False),
            onboarding_model_adapters=(OpenAICompatibleOnboardingAdapter(),),
            distributed_runtime=distributed,
        )
        admin = deployment.bootstrap_admin("admin", _PASSWORD)
        token = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="issue-46-worker-artifact-verification",
        ).secret

        configured = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/onboarding.configure-model",
                headers=_headers(token, key="issue-46:full:model"),
                body=_model_payload(f"http://{host}:{port}/v1"),
            )
        )
        assert configured.status == 200, configured.body

        project_response = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/projects",
                headers=_headers(token, key="issue-46:full:project"),
                body={"name": "Worker artifact verification project"},
            )
        )
        assert project_response.status == 201, project_response.body
        assert isinstance(project_response.body, dict)
        project_id = project_response.body["id"]
        assert isinstance(project_id, str)

        workspace_response = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/workspaces",
                headers=_headers(token, key="issue-46:full:workspace"),
                body={"project_id": project_id, "workspace_type": "isolated_run"},
            )
        )
        assert workspace_response.status == 201, workspace_response.body
        assert isinstance(workspace_response.body, dict)
        workspace_id = workspace_response.body["id"]
        workspace_snapshot_id = workspace_response.body["base_snapshot_id"]
        assert isinstance(workspace_id, str)
        assert isinstance(workspace_snapshot_id, str)

        node_id = new_id("node")
        worker_id = new_id("worker")
        distributed.register(
            RegistrationRequest(
                node=NodeRecord(node_id=node_id, display_name="issue-46-artifact-node"),
                workers=(
                    WorkerRecord(
                        worker_id=worker_id,
                        node_id=node_id,
                        supported_executors=("reference",),
                        capability_refs=(WORKSPACE_ARTIFACT_CAPABILITY_ID,),
                    ),
                ),
            )
        )

        worker_root = tmp_path / "worker-root"
        message_transport = InProcessMessageTransport(
            provider_id="issue-46-worker-artifact-vertical"
        )
        store = WorkerWorkspaceMaterializationStore(worker_id, worker_root)
        endpoint_task = asyncio.create_task(
            WorkerWorkspaceTransportEndpoint(store, message_transport).serve()
        )
        await asyncio.sleep(0)

        def workspace_context(workspace: Workspace) -> DataAccessContext:
            return DataAccessContext(
                operation=OperationContext(
                    correlation_id=f"worker-workspace:{workspace.id}",
                    owner_type="user",
                    owner_id=admin.user_id,
                    project_id=workspace.project_id,
                ),
                actor_ref=admin.user_id,
            )

        materializer = TransportRemoteWorkspaceMaterializer(
            worker_id,
            message_transport,
            deployment.workspaces,
            deployment.files,
            workspace_context,
        )

        def execution_workspace(job: WorkerJobRequest) -> str:
            assert job.workspace_ref is not None
            assert job.snapshot_ref is not None
            return store.execution_workspace(job.workspace_ref, job.snapshot_ref)

        materializing = MaterializingWorkerDispatcher(
            ExecutorWorker(
                worker_id,
                ReferenceExecutor(worker_root),
                workspace="unused",
                workspace_resolver=execution_workspace,
            ),
            materializer,
            WorkspaceJobMaterializationResolver(deployment.workspaces),
        )
        artifact_worker = ArtifactPublishingWorkerDispatcher(
            materializing,
            CanonicalWorkspaceArtifactPublisher(
                deployment.workspaces,
                deployment.files,
                deployment.kernel,
                workspace_context,
            ),
        )
        distributed.attach_worker(artifact_worker)
        await deployment.capabilities.register_provider(
            DistributedExecutorArtifactProvider(
                distributed,
                worker_id=worker_id,
                workspace_bindings=deployment.run_workspace_bindings,
            )
        )

        agent = deployment.agents.create_agent(
            _agent_profile(),
            owner_ref=OwnerRef(type="user", id=admin.user_id),
            project_id=project_id,
            workspace_id=workspace_id,
        )
        task_response = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/tasks",
                headers=_headers(token, key="issue-46:full:task"),
                body={
                    "title": "Full Worker Artifact Verification Task",
                    "objective": "Produce Worker-backed evidence and verify that exact evidence.",
                    "project_id": project_id,
                },
            )
        )
        assert task_response.status == 201, task_response.body
        assert isinstance(task_response.body, dict)
        task_id = task_response.body["id"]
        assert isinstance(task_id, str)

        await deployment.kernel.update_task(
            idempotency_key="issue-46:full:agent-binding",
            task_id=task_id,
            metadata=encode_agent_execution_binding(
                AgentExecutionBinding(
                    agent_id=agent.agent_id,
                    agent_revision=agent.revision,
                    model_config_id="model-worker-artifact-vertical",
                    capability_ids=(WORKSPACE_ARTIFACT_CAPABILITY_ID,),
                    workspace_id=workspace_id,
                )
            ),
            actor_ref=admin.user_id,
            source="conformance",
        )

        policy = deployment.verification.register_policy(
            VerificationPolicy(
                name="Worker artifact human verification",
                stages=(
                    VerificationStage(
                        stage_id="human-review",
                        verifier_kind=VerifierKind.HUMAN,
                    ),
                ),
            )
        )
        deployment.verification_runtime.require_task(
            task_id=task_id,
            policy_id=policy.policy_id,
            policy_version=policy.version,
        )

        queued = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:queue",
                headers=_headers(token, key="issue-46:full:queue"),
            )
        )
        assert queued.status == 200, queued.body
        started = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:start",
                headers=_headers(token, key="issue-46:full:start"),
                body={
                    "workspace_id": workspace_id,
                    "workspace_snapshot_id": workspace_snapshot_id,
                },
            )
        )
        assert started.status == 200, started.body
        assert isinstance(started.body, dict)
        run_id = started.body["id"]
        assert isinstance(run_id, str)

        try:
            run = await deployment.kernel.refresh_run(
                idempotency_key="issue-46:full:refresh",
                task_id=task_id,
                run_id=run_id,
                actor_ref=admin.user_id,
                source="conformance",
            )
            assert run.status is RunStatus.SUCCEEDED
            waiting = await deployment.kernel.get_task(task_id)
            assert waiting.status is TaskStatus.WAITING
            assert waiting.wait_reason == "verification:waiting"

            result_id = run.output.get("result_id")
            agent_run_id = run.output.get("agent_run_id")
            artifact_refs = run.output.get("artifact_refs")
            tool_refs = run.output.get("tool_invocation_refs")
            capability_results = run.output.get("capability_results")
            assert isinstance(result_id, str)
            assert isinstance(agent_run_id, str)
            assert isinstance(artifact_refs, tuple) and len(artifact_refs) == 1
            assert isinstance(tool_refs, tuple) and len(tool_refs) == 1
            assert isinstance(capability_results, tuple) and len(capability_results) == 1

            artifact_id = artifact_refs[0]
            tool_invocation_id = tool_refs[0]
            assert isinstance(artifact_id, str)
            assert isinstance(tool_invocation_id, str)
            validate_id(artifact_id, "artifact")
            validate_id(tool_invocation_id, "tool_invocation")

            capability_result = capability_results[0]
            assert isinstance(capability_result, Mapping)
            assert capability_result["capability_id"] == WORKSPACE_ARTIFACT_CAPABILITY_ID
            assert capability_result["canonical_tool_invocation_id"] == tool_invocation_id
            assert capability_result["model_tool_call_id"] == "call-worker-artifact"
            capability_artifacts = capability_result["artifact_refs"]
            assert isinstance(capability_artifacts, tuple | list)
            assert tuple(capability_artifacts) == (artifact_id,)
            evidence_refs = capability_result["evidence_refs"]
            assert isinstance(evidence_refs, tuple | list)
            worker_job_id = evidence_refs[0]
            assert isinstance(worker_job_id, str)
            validate_id(worker_job_id, "worker_job")
            assert artifact_id in evidence_refs

            dispatch = distributed.get_record(worker_job_id)
            lineage = tool_lineage(dispatch.job)
            assert lineage.root_run_id == run_id
            assert lineage.tool_invocation_id == tool_invocation_id
            assert lineage.task_id == task_id
            assert dispatch.worker_id == worker_id
            assert dispatch.job.workspace_ref == workspace_id
            assert dispatch.job.snapshot_ref == workspace_snapshot_id
            assert distributed.registry.get_worker(worker_id).node_id == node_id

            evidence = artifact_worker.evidence(worker_job_id)
            assert evidence.result is not None
            assert evidence.result.artifact_ids == (artifact_id,)
            assert len(evidence.result.changes) == 1
            change = evidence.result.changes[0]
            assert change.kind is WorkspaceChangeKind.CREATED
            assert change.relative_path == _ARTIFACT_PATH
            assert change.file_id is not None
            validate_id(change.file_id, "file")
            assert change.file_id != artifact_id

            file_context = DataAccessContext(
                operation=OperationContext(
                    correlation_id=task_id,
                    owner_type="user",
                    owner_id=admin.user_id,
                    project_id=project_id,
                ),
                actor_ref=admin.user_id,
                task_id=task_id,
                run_id=run_id,
            )
            file_record = await deployment.files.get_file(change.file_id, file_context)
            assert artifact_id in file_record.artifact_ids
            assert await deployment.files.verify_checksum(change.file_id, file_context)
            payload = b"".join(
                [
                    chunk
                    async for chunk in deployment.files.stream_file(change.file_id, file_context)
                ]
            )
            assert payload == _ARTIFACT_CONTENT.encode("utf-8")

            canonical_run = await deployment.kernel.get_run(task_id, run_id)
            canonical_task = await deployment.kernel.get_task(task_id)
            assert artifact_id in canonical_run.artifact_ids
            assert artifact_id in canonical_task.artifact_ids

            agent_run = deployment.agents.repository.get_agent_run(agent_run_id)
            assert agent_run.status is AgentRunStatus.SUCCEEDED
            assert agent_run.run_id == run_id
            assert agent_run.artifact_ids == (artifact_id,)
            assert agent_run.tool_invocation_refs == (tool_invocation_id,)
            assert agent_run.capability_ids == (WORKSPACE_ARTIFACT_CAPABILITY_ID,)

            await deployment.kernel.attach_result(
                idempotency_key="issue-46:full:result",
                task_id=task_id,
                run_id=run_id,
                result_id=result_id,
                actor_ref=admin.user_id,
                source="conformance",
            )
            verification_request = await deployment.verification_runtime.request_verification(
                task_id=task_id,
                policy_id=policy.policy_id,
                policy_version=policy.version,
                stage_id="human-review",
                subject_type="result",
                subject_id=result_id,
                correlation_id=task_id,
            )
            assert verification_request.run_id == run_id
            assert verification_request.result_id == result_id
            assert verification_request.producer is not None
            assert verification_request.producer.agent_id == agent.agent_id
            assert verification_request.capability_ids == (WORKSPACE_ARTIFACT_CAPABILITY_ID,)

            verification_view = await deployment.http.handle(
                HTTPRequest(
                    method="GET",
                    path=f"/api/v1/verifications/{verification_request.verification_id}",
                    headers=_headers(token),
                )
            )
            assert verification_view.status == 200, verification_view.body
            assert isinstance(verification_view.body, dict)
            assert verification_view.body["task_id"] == task_id
            assert verification_view.body["run_id"] == run_id
            assert verification_view.body["result_id"] == result_id
            assert verification_view.body["capability_ids"] == [WORKSPACE_ARTIFACT_CAPABILITY_ID]

            accepted = await deployment.http.handle(
                HTTPRequest(
                    method="POST",
                    path="/api/v1/commands/verification.accept",
                    headers=_headers(token, key="issue-46:full:accept"),
                    body={
                        "resource_ref": verification_request.verification_id,
                        "comment": "Exact Worker-produced canonical Artifact accepted",
                        "evidence_artifact_ids": [artifact_id],
                    },
                )
            )
            assert accepted.status == 200, accepted.body
            assert isinstance(accepted.body, dict)
            verification_result = accepted.body["verification_result"]
            assert isinstance(verification_result, dict)
            assert verification_result["outcome"] == VerificationOutcome.PASS.value
            assert verification_result["evidence_artifact_ids"] == [artifact_id]

            completed = await deployment.kernel.complete_task(
                idempotency_key="issue-46:full:complete",
                task_id=task_id,
                actor_ref=admin.user_id,
                source="conformance",
            )
            assert completed.status is TaskStatus.SUCCEEDED
            final_run = await deployment.kernel.get_run(task_id, run_id)
            assert final_run.run_id == run_id
            assert artifact_id in final_run.artifact_ids
            assert result_id in final_run.result_ids

            task_view = await deployment.http.handle(
                HTTPRequest(
                    method="GET",
                    path=f"/api/v1/tasks/{task_id}",
                    headers=_headers(token),
                )
            )
            run_view = await deployment.http.handle(
                HTTPRequest(
                    method="GET",
                    path=f"/api/v1/runs/{run_id}",
                    headers=_headers(token),
                )
            )
            result_view = await deployment.http.handle(
                HTTPRequest(
                    method="GET",
                    path=f"/api/v1/results/{result_id}",
                    headers=_headers(token),
                )
            )
            timeline = await deployment.http.handle(
                HTTPRequest(
                    method="GET",
                    path=f"/api/v1/tasks/{task_id}/timeline",
                    headers=_headers(token),
                )
            )
            assert task_view.status == run_view.status == result_view.status == timeline.status == 200
            assert isinstance(task_view.body, dict)
            assert isinstance(run_view.body, dict)
            assert isinstance(result_view.body, dict)
            assert isinstance(timeline.body, dict)
            assert task_view.body["status"] == TaskStatus.SUCCEEDED.value
            assert run_view.body["status"] == RunStatus.SUCCEEDED.value
            assert result_view.body["task_id"] == task_id
            assert result_view.body["id"] == result_id

            timeline_items = timeline.body["items"]
            assert isinstance(timeline_items, list)
            event_types = {
                item.get("event_type")
                for item in timeline_items
                if isinstance(item, dict) and item.get("type") == "event"
            }
            telemetry_names = {
                item.get("event_name")
                for item in timeline_items
                if isinstance(item, dict) and item.get("type") == "telemetry"
            }
            assert {
                "run.succeeded",
                "result.attached",
                "artifact.attached",
                "task.succeeded",
            } <= event_types
            assert {"verification.requested", "verification.result_recorded"} <= telemetry_names

            same_run_records = [
                record for record in distributed.records() if record.job.execution.run_id == run_id
            ]
            assert len(same_run_records) == 1
            assert len(_ArtifactToolCallingModelHandler.chat_payloads) == 1
            assert deployment.observability_exporter.logs
            assert deployment.observability_exporter.metrics
        finally:
            endpoint_task.cancel()
            with suppress(asyncio.CancelledError):
                await endpoint_task
            await message_transport.close(graceful=False)

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
