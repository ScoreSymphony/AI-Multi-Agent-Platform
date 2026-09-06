from __future__ import annotations

import asyncio
import json
import threading
from collections.abc import Mapping
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
from ai_multi_agent_platform.capabilities import ECHO_CAPABILITY_ID, NativeEchoProvider
from ai_multi_agent_platform.contracts import JsonValue
from ai_multi_agent_platform.control_plane import HTTPRequest
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef, RunStatus, TaskStatus, validate_id
from ai_multi_agent_platform.models import RoutingRequirements
from ai_multi_agent_platform.onboarding import FIRST_RUN_RESOURCE_ID

_PASSWORD = "correct horse battery staple"
_ECHO_MESSAGE = "local model -> native capability"


class _LocalToolCallingModelHandler(BaseHTTPRequestHandler):
    chat_payloads: list[dict[str, object]] = []

    def do_GET(self) -> None:  # noqa: N802
        if self.path != "/v1/models":
            self.send_error(404)
            return
        self._json(200, {"data": [{"id": "local-tool-model"}]})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/v1/chat/completions":
            self.send_error(404)
            return
        length = int(self.headers.get("content-length", "0"))
        parsed = json.loads(self.rfile.read(length).decode("utf-8"))
        assert isinstance(parsed, dict)
        payload = dict(parsed)
        type(self).chat_payloads.append(payload)

        assert payload["model"] == "local-tool-model"
        tools = payload.get("tools")
        assert isinstance(tools, list)
        assert len(tools) == 1
        tool = tools[0]
        assert isinstance(tool, dict)
        function = tool.get("function")
        assert isinstance(function, dict)
        tool_name = function.get("name")
        parameters = function.get("parameters")
        assert isinstance(tool_name, str)
        assert isinstance(parameters, dict)
        assert parameters.get("required") == ["message"]

        self._json(
            200,
            {
                "model": "local-tool-model",
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call-local-echo",
                                    "type": "function",
                                    "function": {
                                        "name": tool_name,
                                        "arguments": json.dumps({"message": _ECHO_MESSAGE}),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 11,
                    "completion_tokens": 4,
                    "total_tokens": 15,
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
        "provider_id": "local-tool-provider",
        "model_config_id": "model-local-tool-capable",
        "provider_model": "local-tool-model",
        "display_name": "Local Tool Calling Model",
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
        name="Local Capability Agent",
        role="local-capability-agent",
        instructions=AgentInstructions(
            role=InstructionSource(
                content="Use the supplied canonical capability to fulfill the Task."
            )
        ),
        model=AgentModelPolicy(
            requirements=RoutingRequirements(modalities=("text",), tool_calling=True),
            allow_task_override=True,
        ),
        capabilities=AgentCapabilityPolicy(
            allowed=(ECHO_CAPABILITY_ID,),
            constraints=(
                CapabilityConstraint(
                    capability_id=ECHO_CAPABILITY_ID,
                    required=True,
                    exact_version="1.0",
                ),
            ),
        ),
    )


def test_authenticated_local_model_executes_native_capability_end_to_end(tmp_path: Path) -> None:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _LocalToolCallingModelHandler)
    _LocalToolCallingModelHandler.chat_payloads.clear()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def scenario() -> None:
        host, port = server.server_address
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "platform", secure_cookie=False),
            onboarding_model_adapters=(OpenAICompatibleOnboardingAdapter(),),
        )
        await deployment.capabilities.register_provider(NativeEchoProvider())

        admin = deployment.bootstrap_admin("admin", _PASSWORD)
        credential = deployment.authentication.create_personal_access_token(
            admin.user_id,
            purpose="issue-46-local-model-native-capability",
        )
        token = credential.secret

        configured = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/commands/onboarding.configure-model",
                headers=_headers(token, key="issue-46:d:model"),
                body=_model_payload(f"http://{host}:{port}/v1"),
            )
        )
        assert configured.status == 200, configured.body

        project_response = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/projects",
                headers=_headers(token, key="issue-46:d:project"),
                body={"name": "Local model capability project"},
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
                headers=_headers(token, key="issue-46:d:workspace"),
                body={"project_id": project_id, "workspace_type": "isolated_run"},
            )
        )
        assert workspace_response.status == 201, workspace_response.body
        assert isinstance(workspace_response.body, dict)
        workspace_id = workspace_response.body["id"]
        workspace_snapshot_id = workspace_response.body["base_snapshot_id"]
        assert isinstance(workspace_id, str)
        assert isinstance(workspace_snapshot_id, str)

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
                headers=_headers(token, key="issue-46:d:task"),
                body={
                    "title": "Local model native capability Task",
                    "objective": "Echo one message through the native capability.",
                    "project_id": project_id,
                },
            )
        )
        assert task_response.status == 201, task_response.body
        assert isinstance(task_response.body, dict)
        task_id = task_response.body["id"]
        assert isinstance(task_id, str)

        await deployment.kernel.update_task(
            idempotency_key="issue-46:d:agent-binding",
            task_id=task_id,
            metadata=encode_agent_execution_binding(
                AgentExecutionBinding(
                    agent_id=agent.agent_id,
                    agent_revision=agent.revision,
                    model_config_id="model-local-tool-capable",
                    capability_ids=(ECHO_CAPABILITY_ID,),
                    workspace_id=workspace_id,
                )
            ),
            actor_ref=admin.user_id,
            source="conformance",
        )

        queued = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:queue",
                headers=_headers(token, key="issue-46:d:queue"),
            )
        )
        assert queued.status == 200, queued.body

        started = await deployment.http.handle(
            HTTPRequest(
                method="POST",
                path=f"/api/v1/tasks/{task_id}:start",
                headers=_headers(token, key="issue-46:d:start"),
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

        run = await deployment.kernel.refresh_run(
            idempotency_key="issue-46:d:refresh",
            task_id=task_id,
            run_id=run_id,
            actor_ref=admin.user_id,
            source="conformance",
        )
        assert run.status is RunStatus.SUCCEEDED
        task = await deployment.kernel.get_task(task_id)
        assert task.status is TaskStatus.SUCCEEDED

        agent_run_id = run.output.get("agent_run_id")
        result_id = run.output.get("result_id")
        assert isinstance(agent_run_id, str)
        assert isinstance(result_id, str)
        assert run.output.get("model_ref") == "model-local-tool-capable"
        tool_invocation_refs = run.output.get("tool_invocation_refs")
        assert isinstance(tool_invocation_refs, tuple)
        assert len(tool_invocation_refs) == 1
        tool_invocation_id = tool_invocation_refs[0]
        assert isinstance(tool_invocation_id, str)
        validate_id(tool_invocation_id, "tool_invocation")
        assert tool_invocation_id != f"{run_id}:call-local-echo"
        assert _ECHO_MESSAGE in str(run.output.get("text"))

        capability_results = run.output.get("capability_results")
        assert isinstance(capability_results, tuple)
        assert len(capability_results) == 1
        capability_result = capability_results[0]
        assert isinstance(capability_result, Mapping)
        assert capability_result["invocation_id"] == f"{run_id}:capability:1"
        assert capability_result["canonical_tool_invocation_id"] == tool_invocation_id
        assert capability_result["model_tool_call_id"] == "call-local-echo"
        assert capability_result["capability_id"] == ECHO_CAPABILITY_ID
        assert capability_result["capability_version"] == "1.0"
        assert capability_result["provider_id"] == "native.reference"
        assert capability_result["status"] == "succeeded"
        assert capability_result["output"] == {"message": _ECHO_MESSAGE}

        agent_run = deployment.agents.repository.get_agent_run(agent_run_id)
        assert agent_run.status is AgentRunStatus.SUCCEEDED
        assert agent_run.task_id == task_id
        assert agent_run.run_id == run_id
        assert agent_run.selected_model_config_id == "model-local-tool-capable"
        assert agent_run.selected_provider_id == "local-tool-provider"
        assert agent_run.capability_ids == (ECHO_CAPABILITY_ID,)
        assert dict(agent_run.capability_versions) == {ECHO_CAPABILITY_ID: "1.0"}
        assert agent_run.model_call_refs == (f"{run_id}:model",)
        assert agent_run.tool_invocation_refs == (tool_invocation_id,)
        assert agent_run.result_ids == (result_id,)
        assert agent_run.telemetry["capability_invocation_count"] == 1
        assert agent_run.telemetry["model_usage"] == {
            "prompt_tokens": 11,
            "completion_tokens": 4,
            "total_tokens": 15,
        }

        assert len(_LocalToolCallingModelHandler.chat_payloads) == 1
        model_payload = _LocalToolCallingModelHandler.chat_payloads[0]
        assert model_payload["model"] == "local-tool-model"
        messages = model_payload.get("messages")
        assert isinstance(messages, list)
        assert [message["role"] for message in messages if isinstance(message, dict)] == [
            "system",
            "user",
        ]
        tools = model_payload.get("tools")
        assert isinstance(tools, list)
        assert len(tools) == 1

        run_view = await deployment.http.handle(
            HTTPRequest(
                method="GET",
                path=f"/api/v1/runs/{run_id}",
                headers=_headers(token),
            )
        )
        assert run_view.status == 200, run_view.body
        assert isinstance(run_view.body, dict)
        assert run_view.body["id"] == run_id
        assert run_view.body["task_id"] == task_id
        assert run_view.body["status"] == RunStatus.SUCCEEDED.value

    try:
        asyncio.run(scenario())
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
