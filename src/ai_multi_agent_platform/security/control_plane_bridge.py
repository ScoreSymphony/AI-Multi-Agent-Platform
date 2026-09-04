"""Compatibility bridge from the v1 Control Plane to the canonical authorization gate."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from ai_multi_agent_platform.contracts import (
    AuthorizationDecision,
    AuthorizationProvider,
    HealthStatus,
    JsonValue,
    ProviderDescriptor,
)
from ai_multi_agent_platform.contracts.authorization import AuthorizationOutcome
from ai_multi_agent_platform.contracts.authorization import (
    AuthorizationRequest as CanonicalAuthorizationRequest,
)
from ai_multi_agent_platform.contracts.types import AuthorizationRequest as BaseAuthorizationRequest

from .authorization import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    AuthorizationContext,
    ProposedAction,
    ResourceType,
    infer_actor_identity,
)
from .enforcement import AuthorizationGate

_TOKEN_AUTHENTICATION_METHODS = frozenset(
    {
        "personal_access_token",
        "service_token",
        "worker_token",
        "automation_token",
        "integration_token",
    }
)
_SCOPE_FIELDS = frozenset({"actions", "resource_types", "resource_ids"})


class ControlPlaneAuthorizationBridge(AuthorizationProvider):
    """Translate northbound operations into canonical #15 authorization decisions."""

    def __init__(self, gate: AuthorizationGate) -> None:
        self._gate = gate

    @property
    def descriptor(self) -> ProviderDescriptor:
        inner = self._gate.provider.descriptor
        return ProviderDescriptor(
            provider_id=f"{inner.provider_id}:control-plane-gate",
            provider_type="authorization",
            supported_operations=("authorize",),
            health=inner.health,
            available=inner.available,
        )

    async def health(self) -> HealthStatus:
        return await self._gate.provider.health()

    async def authorize(self, request: BaseAuthorizationRequest) -> AuthorizationDecision:
        action, resource_type = canonical_control_plane_vocabulary(request.action)
        scope_denial = credential_scope_denial(request, action, resource_type)
        if scope_denial is not None:
            return AuthorizationDecision(
                AuthorizationOutcome.DENY,
                reason=scope_denial,
                policy_id="credential-scope",
            )

        if isinstance(request, CanonicalAuthorizationRequest):
            task_id = request.task_id or (
                request.resource_ref if request.resource_ref.startswith("task_") else None
            )
            run_id = request.run_id or (
                request.resource_ref if request.resource_ref.startswith("run_") else None
            )
            organization_id = request.organization_id
            team_id = request.team_id
            workspace_id = request.workspace_id
            agent_id = request.agent_id
            capability_ref = request.capability_ref
            side_effect = request.side_effect or request.action
            security_labels = request.security_labels
            node_id = request.node_id
            trust_context = request.trust_context
            request_payload_digest = request.request_payload_digest
        else:
            task_id = request.resource_ref if request.resource_ref.startswith("task_") else None
            run_id = request.resource_ref if request.resource_ref.startswith("run_") else None
            organization_id = None
            team_id = None
            workspace_id = None
            agent_id = None
            capability_ref = None
            side_effect = request.action
            security_labels = ()
            node_id = None
            trust_context = {}
            request_payload_digest = None

        proposed_payload: dict[str, JsonValue] = {"northbound_action": request.action}
        if request_payload_digest is not None:
            proposed_payload["request_payload_sha256"] = request_payload_digest
        actor = _actor_from_request(request)
        proposed = ProposedAction(
            AuthorizationContext(
                actor=actor,
                action=action,
                resource_type=resource_type,
                resource_id=request.resource_ref,
                operation=request.context,
                organization_id=organization_id,
                team_id=team_id,
                workspace_id=workspace_id,
                task_id=task_id,
                run_id=run_id,
                agent_id=agent_id,
                capability_ref=capability_ref,
                side_effect=side_effect,
                security_labels=security_labels,
                node_id=node_id,
                trust_context=trust_context,
            ),
            payload=proposed_payload,
        )
        return await self._gate.decide(proposed)


def credential_scope_denial(
    request: BaseAuthorizationRequest,
    action: AuthorizationAction,
    resource_type: ResourceType,
) -> str | None:
    """Return a deny reason when an authenticated credential ceiling rejects an action.

    The scope is a deny-only constraint. Passing this check never grants the operation;
    the normal #15 provider still evaluates the canonical authorization request.
    """

    if not isinstance(request, CanonicalAuthorizationRequest):
        return None
    authentication = request.trust_context.get("authentication")
    if authentication is None:
        return None
    if not isinstance(authentication, Mapping):
        return "authenticated credential context is malformed"
    method = authentication.get("method")
    if method not in _TOKEN_AUTHENTICATION_METHODS:
        return None
    credential_id = authentication.get("credential_id")
    if not isinstance(credential_id, str) or not credential_id.strip():
        return "authenticated token credential reference is missing"
    raw_scope = authentication.get("credential_scope")
    if not isinstance(raw_scope, Mapping) or set(raw_scope) != _SCOPE_FIELDS:
        return "authenticated credential scope is missing or malformed"

    actions = _scope_strings(raw_scope.get("actions"))
    resource_types = _scope_strings(raw_scope.get("resource_types"))
    resource_ids = _scope_strings(raw_scope.get("resource_ids"))
    if actions is None or resource_types is None or resource_ids is None:
        return "authenticated credential scope is malformed"
    if actions and action.value not in actions:
        return "credential scope does not permit this authorization action"
    if resource_types and resource_type.value not in resource_types:
        return "credential scope does not permit this resource type"
    if resource_ids and request.resource_ref not in resource_ids:
        return "credential scope does not permit this resource"
    return None


def canonical_control_plane_vocabulary(action: str) -> tuple[AuthorizationAction, ResourceType]:
    if action.startswith("task-management."):
        return AuthorizationAction.MODIFY, ResourceType.TASK

    if action.startswith("automation."):
        automation_verb = action.removeprefix("automation.")
        automation_actions = {
            "create": AuthorizationAction.CREATE,
            "update": AuthorizationAction.MODIFY,
            "pause": AuthorizationAction.ADMINISTER,
            "resume": AuthorizationAction.ADMINISTER,
            "disable": AuthorizationAction.ADMINISTER,
            "test": AuthorizationAction.EXECUTE,
            "webhook": AuthorizationAction.EXECUTE,
            "event": AuthorizationAction.EXECUTE,
            "evaluate": AuthorizationAction.EXECUTE,
            "retry-delivery": AuthorizationAction.EXECUTE,
        }
        return automation_actions.get(
            automation_verb, AuthorizationAction.MODIFY
        ), ResourceType.AUTOMATION

    if action.startswith("notification."):
        notification_verb = action.removeprefix("notification.")
        notification_actions = {
            "mark-read": AuthorizationAction.MODIFY,
            "mark-all-read": AuthorizationAction.MODIFY,
            "acknowledge": AuthorizationAction.MODIFY,
            "dismiss": AuthorizationAction.MODIFY,
            "archive": AuthorizationAction.MODIFY,
            "preference.update": AuthorizationAction.MODIFY,
            "delivery.retry": AuthorizationAction.EXECUTE,
        }
        return notification_actions.get(
            notification_verb, AuthorizationAction.MODIFY
        ), ResourceType.NOTIFICATION

    if action.startswith("terminal.session."):
        terminal_verb = action.removeprefix("terminal.session.")
        terminal_actions = {
            "create": AuthorizationAction.CREATE,
            "input": AuthorizationAction.EXECUTE,
            "resize": AuthorizationAction.MODIFY,
            "terminate": AuthorizationAction.EXECUTE,
        }
        return terminal_actions.get(terminal_verb, AuthorizationAction.MODIFY), ResourceType.GENERIC

    if action.startswith("plugin."):
        plugin_verb = action.removeprefix("plugin.")
        plugin_actions = {
            "install": AuthorizationAction.CREATE,
            "configure": AuthorizationAction.MODIFY,
            "enable": AuthorizationAction.ADMINISTER,
            "disable": AuthorizationAction.ADMINISTER,
            "refresh-health": AuthorizationAction.ADMINISTER,
            "validate-update": AuthorizationAction.READ,
            "remove": AuthorizationAction.DELETE,
        }
        return plugin_actions.get(plugin_verb, AuthorizationAction.MODIFY), ResourceType.PLUGIN

    if action.startswith("node."):
        node_verb = action.removeprefix("node.")
        node_actions = {
            "drain": AuthorizationAction.ADMINISTER,
            "undrain": AuthorizationAction.ADMINISTER,
            "maintenance-enable": AuthorizationAction.ADMINISTER,
            "maintenance-disable": AuthorizationAction.ADMINISTER,
        }
        return node_actions.get(node_verb, AuthorizationAction.MODIFY), ResourceType.NODE

    if action.startswith("worker."):
        worker_verb = action.removeprefix("worker.")
        worker_actions = {
            "drain": AuthorizationAction.ADMINISTER,
            "undrain": AuthorizationAction.ADMINISTER,
        }
        return worker_actions.get(worker_verb, AuthorizationAction.MODIFY), ResourceType.WORKER

    resource_name, separator, verb = action.partition(":")
    if not separator:
        resource_name, verb = "generic", action

    if resource_name == "credential":
        return AuthorizationAction.MANAGE_CREDENTIALS, ResourceType.SECRET_REFERENCE

    action_map = {
        "list": AuthorizationAction.VIEW,
        "read": AuthorizationAction.READ,
        "create": AuthorizationAction.CREATE,
        "update": AuthorizationAction.MODIFY,
        "modify": AuthorizationAction.MODIFY,
        "queue": AuthorizationAction.EXECUTE,
        "start": AuthorizationAction.EXECUTE,
        "retry": AuthorizationAction.EXECUTE,
        "cancel": AuthorizationAction.MODIFY,
        "subscribe": AuthorizationAction.READ,
        "execute": AuthorizationAction.EXECUTE,
        "test": AuthorizationAction.EXECUTE,
        "evaluate": AuthorizationAction.EXECUTE,
        "enable": AuthorizationAction.ADMINISTER,
        "disable": AuthorizationAction.ADMINISTER,
        "pause": AuthorizationAction.ADMINISTER,
        "resume": AuthorizationAction.ADMINISTER,
        "refresh-health": AuthorizationAction.ADMINISTER,
    }
    resource_map = {
        "project": ResourceType.PROJECT,
        "workspace": ResourceType.WORKSPACE,
        "task": ResourceType.TASK,
        "run": ResourceType.RUN,
        "artifact": ResourceType.ARTIFACT,
        "artifacts": ResourceType.ARTIFACT,
        "file": ResourceType.FILE,
        "memory": ResourceType.MEMORY,
        "knowledge-source": ResourceType.KNOWLEDGE_SOURCE,
        "model-provider": ResourceType.PROVIDER_CONFIGURATION,
        "model": ResourceType.MODEL_CONFIGURATION,
        "agent": ResourceType.AGENT,
        "agent-team": ResourceType.AGENT_TEAM,
        "worker": ResourceType.WORKER,
        "node": ResourceType.NODE,
        "automation": ResourceType.AUTOMATION,
        "automation-delivery": ResourceType.AUTOMATION,
        "notification": ResourceType.NOTIFICATION,
        "notification-preference": ResourceType.NOTIFICATION,
        "integration": ResourceType.INTEGRATION,
        "connector": ResourceType.CONNECTOR,
        "plugin": ResourceType.PLUGIN,
        "plugin-candidate": ResourceType.PLUGIN,
    }
    return (
        action_map.get(verb, AuthorizationAction.MODIFY),
        resource_map.get(resource_name, ResourceType.GENERIC),
    )


def _actor_from_request(request: BaseAuthorizationRequest) -> ActorIdentity:
    if isinstance(request, CanonicalAuthorizationRequest):
        try:
            return ActorIdentity(request.principal_ref, ActorType(request.actor_type))
        except ValueError:
            pass
    return infer_actor_identity(request.principal_ref)


def _scope_strings(value: object) -> frozenset[str] | None:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes | bytearray):
        return None
    result: set[str] = set()
    for item in value:
        if not isinstance(item, str) or not item.strip():
            return None
        result.add(item)
    return frozenset(result)
