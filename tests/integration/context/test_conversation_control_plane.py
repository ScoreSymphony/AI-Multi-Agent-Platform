from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from pathlib import Path
from typing import TypeVar, cast

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentRevisionRef,
    AgentService,
    AgentTeamMember,
    AgentTeamProfile,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.contracts import (
    AuthorizationDecision,
    AuthorizationOutcome,
    AuthorizationProvider,
    HealthStatus,
    ProviderDescriptor,
)
from ai_multi_agent_platform.contracts.types import (
    AuthorizationRequest,
    JsonValue,
    OperationContext,
)
from ai_multi_agent_platform.control_plane import (
    ActorContext,
    AuthenticatedControlPlaneHTTP,
    ControlPlane,
    ControlPlaneHTTP,
    HTTPRequest,
    HTTPResponse,
)
from ai_multi_agent_platform.conversations import ConversationService, JsonConversationRepository
from ai_multi_agent_platform.data import DataAccessContext, FileProvider, LocalFileProvider
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.security import (
    InMemoryAuthenticationStore,
    LocalAuthenticationService,
    ScryptPasswordHasher,
)
from ai_multi_agent_platform.testing import FakeLifecycleBackend, FakeOrchestrator

T = TypeVar("T")

JSON_HEADERS = {"content-type": "application/json"}
ALICE = ActorContext(
    principal_ref="user:alice",
    owner_type="user",
    owner_id="alice",
    actor_type="human",
)
BOB = ActorContext(
    principal_ref="user:bob",
    owner_type="user",
    owner_id="bob",
    actor_type="human",
)


def _run(value: Awaitable[T]) -> T:  # noqa: UP047
    return asyncio.run(value)


def _stack(
    tmp_path: Path,
    *,
    authorization: AuthorizationProvider | None = None,
    agent_service: AgentService | None = None,
    file_provider: FileProvider | None = None,
) -> tuple[ControlPlane, ControlPlaneHTTP, ConversationService, PlatformKernel]:
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    conversations = ConversationService(JsonConversationRepository(tmp_path / "conversations.json"))
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=authorization,
        conversation_service=conversations,
        conversation_agent_service=agent_service,
        conversation_file_provider=file_provider,
    )
    return control_plane, ControlPlaneHTTP(control_plane), conversations, kernel


def _post(
    http: ControlPlaneHTTP,
    path: str,
    body: dict[str, JsonValue],
    *,
    actor: ActorContext = ALICE,
    key: str,
) -> HTTPResponse:
    return _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path=path,
                headers={**JSON_HEADERS, "idempotency-key": key},
                body=body,
                trusted_actor=actor,
            )
        )
    )


def _get(http: ControlPlaneHTTP, path: str, *, actor: ActorContext = ALICE) -> HTTPResponse:
    return _run(http.handle(HTTPRequest(method="GET", path=path, trusted_actor=actor)))


def _project(http: ControlPlaneHTTP, *, key: str = "project-create") -> str:
    response = _post(http, "/api/v1/projects", {"name": "Conversation Project"}, key=key)
    assert response.status == 201
    project_id = response.body["id"]
    assert isinstance(project_id, str)
    return project_id


def _conversation(
    http: ControlPlaneHTTP,
    *,
    project_id: str | None = None,
    target: dict[str, JsonValue] | None = None,
    key: str = "conversation-create",
) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {"title": "Issue 72"}
    if project_id is not None:
        body["project_id"] = project_id
    if target is not None:
        body["target"] = target
    response = _post(http, "/api/v1/conversations", body, key=key)
    assert response.status == 201, response.body
    return cast(dict[str, JsonValue], response.body)


def _message(
    http: ControlPlaneHTTP,
    conversation_id: str,
    *,
    references: list[JsonValue] | None = None,
    key: str = "message-create",
) -> dict[str, JsonValue]:
    body: dict[str, JsonValue] = {
        "content": [{"kind": "text", "text": "Please turn this into durable work."}],
    }
    if references is not None:
        body["references"] = references
    response = _post(
        http,
        f"/api/v1/conversations/{conversation_id}/messages",
        body,
        key=key,
    )
    assert response.status == 201, response.body
    return cast(dict[str, JsonValue], response.body)


def _agent_profile(name: str) -> AgentProfile:
    return AgentProfile(
        name=name,
        role="assistant",
        instructions=AgentInstructions(
            role=InstructionSource(content="Assist through canonical tasks.", version="1"),
        ),
    )


def test_project_and_task_target_conversations_use_canonical_context(tmp_path: Path) -> None:
    _, http, _, _ = _stack(tmp_path)
    project_id = _project(http)
    task = _post(
        http,
        "/api/v1/tasks",
        {"title": "Existing", "objective": "Existing canonical task", "project_id": project_id},
        key="task-create",
    )
    assert task.status == 201
    task_id = task.body["id"]
    assert isinstance(task_id, str)

    targeted = _conversation(
        http,
        target={"kind": "task", "id": task_id},
    )
    assert targeted["project_id"] == project_id
    assert targeted["metadata"] == {"target": {"kind": "task", "id": task_id}}

    project_targeted = _conversation(
        http,
        target={"kind": "project", "id": project_id},
        key="conversation-project-target",
    )
    assert project_targeted["project_id"] == project_id


def test_agent_and_team_targets_pin_canonical_revisions(tmp_path: Path) -> None:
    agents = AgentService(InMemoryAgentRepository())
    _, http, _, _ = _stack(tmp_path, agent_service=agents)
    project_id = _project(http, key="agent-project-create")
    owner = OwnerRef(type="user", id="alice")
    agent = agents.create_agent(
        _agent_profile("Target Agent"),
        owner_ref=owner,
        project_id=project_id,
    )
    team = agents.create_team(
        AgentTeamProfile(
            name="Target Team",
            members=(
                AgentTeamMember(
                    agent=AgentRevisionRef(agent.agent_id, agent.revision),
                    role="lead",
                ),
            ),
            leader_agent_id=agent.agent_id,
        ),
        owner_ref=owner,
        project_id=project_id,
    )

    agent_conversation = _conversation(
        http,
        target={"kind": "agent", "id": agent.agent_id, "revision": agent.revision},
        key="agent-conversation",
    )
    assert agent_conversation["default_agent"] == {
        "kind": "agent",
        "id": agent.agent_id,
        "revision": agent.revision,
    }

    team_conversation = _conversation(
        http,
        target={"kind": "agent_team", "id": team.team_id, "revision": team.revision},
        key="team-conversation",
    )
    assert team_conversation["default_agent"] == {
        "kind": "agent_team",
        "id": team.team_id,
        "revision": team.revision,
    }


def test_message_to_task_handoff_is_canonical_and_bidirectionally_linked(tmp_path: Path) -> None:
    _, http, _, kernel = _stack(tmp_path)
    project_id = _project(http)
    conversation = _conversation(http, project_id=project_id)
    conversation_id = cast(str, conversation["id"])
    message = _message(http, conversation_id)
    message_id = cast(str, message["id"])

    handoff = _post(
        http,
        f"/api/v1/conversation-messages/{message_id}:create-task",
        {"title": "From chat", "objective": "Canonical durable work"},
        key="handoff-task",
    )
    assert handoff.status == 201, handoff.body
    task = cast(dict[str, JsonValue], handoff.body["task"])
    task_id = cast(str, task["id"])
    assert task["metadata"] == {
        "conversation_id": conversation_id,
        "conversation_message_id": message_id,
    }
    state = _run(kernel.get_task(task_id))
    assert dict(state.task.metadata) == {
        "conversation_id": conversation_id,
        "conversation_message_id": message_id,
    }
    linked_message = cast(dict[str, JsonValue], handoff.body["message"])
    assert any(
        reference["kind"] == "task" and reference["id"] == task_id
        for reference in linked_message["references"]
    )


def test_existing_task_can_be_attached_to_message_without_second_task(tmp_path: Path) -> None:
    _, http, _, kernel = _stack(tmp_path)
    project_id = _project(http)
    task = _post(
        http,
        "/api/v1/tasks",
        {"title": "Existing", "objective": "Attach only", "project_id": project_id},
        key="existing-task",
    )
    task_id = cast(str, task.body["id"])
    conversation = _conversation(http, project_id=project_id)
    message = _message(http, cast(str, conversation["id"]))

    attached = _post(
        http,
        f"/api/v1/conversation-messages/{message['id']}:attach-task",
        {"task_id": task_id},
        key="attach-existing",
    )
    assert attached.status == 200
    stream_ids = _run(kernel._repository.list_stream_ids())
    assert stream_ids.count(task_id) == 1
    linked = cast(dict[str, JsonValue], attached.body["message"])
    assert {"kind": "task", "id": task_id, "label": None, "metadata": {}} in linked["references"]


def test_file_attachments_use_canonical_file_provider_and_enforce_project_scope(
    tmp_path: Path,
) -> None:
    files = LocalFileProvider(tmp_path / "files", tmp_path / "files.sqlite3")
    _, http, _, _ = _stack(tmp_path / "platform", file_provider=files)
    project_id = _project(http)
    conversation = _conversation(http, project_id=project_id)
    context = DataAccessContext(
        operation=OperationContext(
            correlation_id="file-create",
            owner_type="user",
            owner_id="alice",
            project_id=project_id,
        ),
        actor_ref=ALICE.principal_ref,
    )
    record = _run(files.create_file(b"hello", context, content_type="text/plain"))
    accepted = _message(
        http,
        cast(str, conversation["id"]),
        references=[{"kind": "file", "id": record.file_id}],
        key="file-message",
    )
    assert accepted["references"][0]["id"] == record.file_id

    other_project = new_id("project")
    other_record = _run(
        files.create_file(
            b"private",
            DataAccessContext(
                operation=OperationContext(
                    correlation_id="other-file",
                    owner_type="user",
                    owner_id="alice",
                    project_id=other_project,
                ),
                actor_ref=ALICE.principal_ref,
            ),
        )
    )
    denied = _post(
        http,
        f"/api/v1/conversations/{conversation['id']}/messages",
        {
            "content": [{"kind": "text", "text": "cross project"}],
            "references": [{"kind": "file", "id": other_record.file_id}],
        },
        key="cross-project-file",
    )
    assert denied.status == 403


class _ProjectIsolationAuthorization:
    def __init__(self, allowed: dict[str, str]) -> None:
        self.allowed = allowed
        self.descriptor = ProviderDescriptor(
            provider_id="issue-72-project-isolation",
            provider_type="authorization",
            supported_operations=("authorize",),
            health=HealthStatus.HEALTHY,
        )

    async def health(self) -> HealthStatus:
        return HealthStatus.HEALTHY

    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        project_id = request.context.project_id
        if project_id is not None and self.allowed.get(request.principal_ref) != project_id:
            return AuthorizationDecision(
                AuthorizationOutcome.DENY,
                reason="project boundary denied",
            )
        return AuthorizationDecision(AuthorizationOutcome.ALLOW)


def test_project_and_private_conversation_isolation(tmp_path: Path) -> None:
    project_a = new_id("project")
    project_b = new_id("project")
    authorization = _ProjectIsolationAuthorization(
        {ALICE.principal_ref: project_a, BOB.principal_ref: project_b}
    )
    _, http, conversations, _ = _stack(tmp_path, authorization=authorization)
    private = _run(
        conversations.create_conversation(title="Alice private", owner_ref=ALICE.principal_ref)
    )
    project_conversation = _run(
        conversations.create_conversation(
            title="Project A",
            owner_ref=ALICE.principal_ref,
            project_id=project_a,
        )
    )

    private_denied = _get(http, f"/api/v1/conversations/{private.id}", actor=BOB)
    assert private_denied.status == 403
    project_denied = _get(http, f"/api/v1/conversations/{project_conversation.id}", actor=BOB)
    assert project_denied.status == 403

    bob_list = _get(http, "/api/v1/conversations", actor=BOB)
    assert bob_list.status == 200
    assert bob_list.body["items"] == []


def test_sender_spoofing_and_non_user_role_are_rejected(tmp_path: Path) -> None:
    _, http, _, _ = _stack(tmp_path)
    conversation = _conversation(http)
    conversation_id = cast(str, conversation["id"])
    spoofed = _post(
        http,
        f"/api/v1/conversations/{conversation_id}/messages",
        {
            "sender_ref": "service:admin",
            "content": [{"kind": "text", "text": "spoof"}],
        },
        key="spoof",
    )
    assert spoofed.status == 400

    assistant = _post(
        http,
        f"/api/v1/conversations/{conversation_id}/messages",
        {
            "role": "assistant",
            "content": [{"kind": "text", "text": "pretend assistant"}],
        },
        key="role-spoof",
    )
    assert assistant.status == 403


def test_openapi_exposes_chat_routes_without_provider_private_sessions(tmp_path: Path) -> None:
    _, http, _, _ = _stack(tmp_path)
    response = _get(http, "/api/v1/openapi.json")
    assert response.status == 200
    assert "/api/v1/conversations/{conversation_id}/messages" in response.body["paths"]
    assert response.body["x-conversation-task-centric"] is True
    assert response.body["x-conversation-provider-private-sessions-canonical"] is False
    serialized = str(response.body).lower()
    assert "provider_session_id" not in serialized
    assert "backend_ref" not in serialized


def test_authentication_boundary_protects_conversation_routes(tmp_path: Path) -> None:
    control_plane, _, _, _ = _stack(tmp_path)
    authentication = LocalAuthenticationService(
        store=InMemoryAuthenticationStore(),
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024),
    )
    http = AuthenticatedControlPlaneHTTP(control_plane, authentication, secure_cookie=False)
    denied = _run(
        http.handle(
            HTTPRequest(
                method="POST",
                path="/api/v1/conversations",
                headers={**JSON_HEADERS, "idempotency-key": "unauthenticated-chat"},
                body={"title": "Denied"},
            )
        )
    )
    assert denied.status == 401
