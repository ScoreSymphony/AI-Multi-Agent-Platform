"""First-run onboarding state and local/self-hosted model golden-path service."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import replace
from ipaddress import ip_address
from typing import cast
from urllib.parse import urlsplit

from ai_multi_agent_platform.agents import STARTER_CATALOG_SOURCE, AgentRuntime, AgentService
from ai_multi_agent_platform.contracts import (
    AdapterMetadata,
    ContractError,
    ErrorCode,
    HealthStatus,
)
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.control_plane.service import ScopeStore
from ai_multi_agent_platform.models import (
    JsonModelRegistryStore,
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRegistry,
)
from ai_multi_agent_platform.security import SecretReference

from .agent_lifecycle import preflight_first_run_agent
from .persistence import (
    JsonModelProviderSetupStore,
    JsonOnboardingCommandStore,
    ModelProviderSetupRecord,
    OnboardingCommandRecord,
)
from .providers import OnboardingModelAdapter, OnboardingModelEndpoint

FIRST_RUN_RESOURCE_ID = "first-run"
ONBOARDING_COLLECTION = "onboarding"
ONBOARDING_CONFIGURE_MODEL_COMMAND = "onboarding.configure-model"
ONBOARDING_COMMANDS = (ONBOARDING_CONFIGURE_MODEL_COMMAND,)

_ROUTABLE_HEALTH = frozenset({HealthStatus.HEALTHY, HealthStatus.DEGRADED})
_PLAINTEXT_CREDENTIAL_KEYS = frozenset(
    {
        "apikey",
        "authorization",
        "bearertoken",
        "credential",
        "password",
        "secret",
        "token",
    }
)


class OnboardingService:
    """Compose existing canonical subsystems into one explainable first-run path.

    This service does not own Project, Workspace, Agent or Task lifecycles. It reports
    their readiness and owns only the safe setup metadata required to attach a model
    adapter to the existing #10 ModelRegistry.
    """

    def __init__(
        self,
        *,
        models: ModelRegistry,
        model_store: JsonModelRegistryStore,
        provider_store: JsonModelProviderSetupStore,
        scopes: ScopeStore,
        agents: AgentService,
        agent_runtime: AgentRuntime,
        model_adapters: Iterable[OnboardingModelAdapter] = (),
        command_store: JsonOnboardingCommandStore | None = None,
    ) -> None:
        if agent_runtime.service is not agents:
            raise ValueError("onboarding AgentRuntime must use the supplied AgentService")
        if agent_runtime.model_registry is not models:
            raise ValueError("onboarding AgentRuntime must use the supplied ModelRegistry")
        self.models = models
        self.model_store = model_store
        self.provider_store = provider_store
        self.command_store = command_store
        self.scopes = scopes
        self.agents = agents
        self.agent_runtime = agent_runtime
        self.model_adapters: dict[str, OnboardingModelAdapter] = {}
        for adapter in model_adapters:
            if not adapter.adapter_id.strip():
                raise ValueError("onboarding model adapter_id must not be blank")
            if adapter.adapter_id in self.model_adapters:
                raise ValueError(f"duplicate onboarding model adapter: {adapter.adapter_id}")
            self.model_adapters[adapter.adapter_id] = adapter
        self._provider_records: dict[str, ModelProviderSetupRecord] = {}
        self._command_records: dict[tuple[str, str], OnboardingCommandRecord] = {}

    def restore(self) -> None:
        """Restore adapter attachments, command replays and canonical model inventory."""

        records = self.provider_store.load()
        for provider_record in records:
            adapter = self._model_adapter(provider_record.adapter_id)
            provider = adapter.build_provider(
                OnboardingModelEndpoint(
                    provider_id=provider_record.provider_id,
                    base_url=provider_record.base_url,
                    models=provider_record.models,
                    credential_ref=provider_record.credential_ref,
                )
            )
            self.models.register_provider(provider)
            self._provider_records[provider_record.provider_id] = provider_record
        if self.command_store is not None:
            for command_record in self.command_store.load():
                self._command_records[command_record.replay_key] = command_record
        if self.model_store.path.exists():
            self.model_store.restore(self.models)

    async def configure_model(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        """Validate and persist one explicit local/self-hosted provider route."""

        if resource_ref != FIRST_RUN_RESOURCE_ID:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"onboarding model setup requires resource_ref={FIRST_RUN_RESOURCE_ID!r}",
            )
        _reject_credentials(payload)
        idempotency_key = context.idempotency_key
        if idempotency_key is None:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "onboarding.configure-model requires an idempotency key",
            )
        payload_digest = _payload_digest(payload)
        replay_key = (context.actor.principal_ref, idempotency_key)
        replay = self._command_records.get(replay_key)
        if replay is not None:
            if (
                replay.command != ONBOARDING_CONFIGURE_MODEL_COMMAND
                or replay.resource_ref != resource_ref
                or replay.payload_digest != payload_digest
            ):
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "idempotency key was already used for a different onboarding model command",
                    details={"idempotency_key": idempotency_key},
                )
            return dict(replay.result)

        adapter_id = _required_string(payload, "adapter_id")
        adapter = self._model_adapter(adapter_id)
        provider_id = _required_string(payload, "provider_id")
        model_config_id = _required_string(payload, "model_config_id")
        provider_model = _required_string(payload, "provider_model")
        display_name = _optional_string(payload, "display_name") or model_config_id
        base_url = _validated_base_url(_required_string(payload, "base_url"))
        location = _golden_path_location(_required_string(payload, "location"))
        if location is ModelLocation.LOCAL:
            _require_loopback_endpoint(base_url)

        current_record = self._provider_records.get(provider_id)
        if "credential_ref" in payload:
            credential_ref = _optional_secret_reference(payload.get("credential_ref"))
        else:
            credential_ref = current_record.credential_ref if current_record is not None else None
        mappings = dict(current_record.models) if current_record is not None else {}
        mappings[model_config_id] = provider_model
        candidate_record = ModelProviderSetupRecord(
            provider_id=provider_id,
            adapter_id=adapter_id,
            base_url=base_url,
            models=mappings,
            credential_ref=credential_ref,
        )
        provider = adapter.build_provider(
            OnboardingModelEndpoint(
                provider_id=provider_id,
                base_url=base_url,
                models=mappings,
                credential_ref=credential_ref,
            )
        )

        health = await provider.health()
        if health is not HealthStatus.HEALTHY:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "model endpoint did not pass the first-run health check",
                retryable=True,
                provider_id=provider_id,
                details={
                    "provider_id": provider_id,
                    "location": location.value,
                    "guidance": (
                        "Verify that the endpoint is running, that any referenced canonical secret "
                        "is provisioned for the selected SecretProvider, and that the installed "
                        "ModelProvider adapter can inspect its native model inventory."
                    ),
                },
            )
        native_models = await adapter.list_native_models(provider)
        if provider_model not in native_models:
            raise ContractError(
                ErrorCode.MODEL_UNAVAILABLE,
                "configured provider model was not reported by the endpoint",
                provider_id=provider_id,
                details={
                    "provider_model": provider_model,
                    "available_provider_models": list(native_models),
                },
            )

        capabilities = _capabilities(payload)
        priority = _optional_integer(payload, "priority", default=0)
        aliases = _string_tuple(payload, "aliases")
        adapter_metadata = (
            AdapterMetadata(
                namespace=adapter_id,
                values={"provider_native_model": provider_model},
            ),
        )
        try:
            current_model = self.models.get_model(model_config_id)
        except ContractError as exc:
            if exc.code is not ErrorCode.NOT_FOUND:
                raise
            configured_model = ModelConfiguration(
                config_id=model_config_id,
                display_name=display_name,
                provider_id=provider_id,
                aliases=aliases,
                location=location,
                health=HealthStatus.HEALTHY,
                priority=priority,
                capabilities=capabilities,
                adapter_metadata=adapter_metadata,
            )
            self.models.register_model(configured_model)
        else:
            if _model_configuration_matches(
                current_model,
                display_name=display_name,
                provider_id=provider_id,
                aliases=aliases,
                location=location,
                priority=priority,
                capabilities=capabilities,
                adapter_metadata=adapter_metadata,
            ):
                configured_model = current_model
            else:
                configured_model = replace(
                    current_model,
                    display_name=display_name,
                    provider_id=provider_id,
                    revision=current_model.revision + 1,
                    aliases=aliases,
                    location=location,
                    health=HealthStatus.HEALTHY,
                    enabled=True,
                    priority=priority,
                    capabilities=capabilities,
                    adapter_metadata=adapter_metadata,
                )
                self.models.update_model(configured_model)

        provider_exists = any(
            item.descriptor.provider_id == provider_id for item in self.models.list_providers()
        )
        if provider_exists:
            self.models.replace_provider(provider)
        else:
            self.models.register_provider(provider)
        self._provider_records[provider_id] = candidate_record
        self.provider_store.save(self._provider_records.values())
        self.model_store.save(self.models)

        result: dict[str, JsonValue] = {
            "id": model_config_id,
            "type": "model",
            "provider_id": provider_id,
            "adapter_id": adapter_id,
            "display_name": display_name,
            "location": location.value,
            "health": HealthStatus.HEALTHY.value,
            "enabled": True,
            "external_paid_provider_selected": False,
            "credential_mode": "secret_reference" if credential_ref is not None else "none",
        }
        command_record = OnboardingCommandRecord(
            principal_ref=context.actor.principal_ref,
            idempotency_key=idempotency_key,
            command=ONBOARDING_CONFIGURE_MODEL_COMMAND,
            resource_ref=resource_ref,
            payload_digest=payload_digest,
            result=result,
        )
        self._command_records[command_record.replay_key] = command_record
        if self.command_store is not None:
            self.command_store.save(self._command_records.values())
        return result

    def status(self, context: RequestContext) -> dict[str, JsonValue]:
        """Return first-run progress using the same preconditions as first-Task execution."""

        owner_type = context.actor.owner_type
        owner_id = context.actor.owner_id
        projects = tuple(
            project
            for project in self.scopes.list_projects()
            if owner_type is not None
            and owner_id is not None
            and project.owner_ref.type == owner_type
            and project.owner_ref.id == owner_id
        )
        project_ids = {project.id for project in projects}
        workspaces = tuple(
            workspace
            for workspace in self.scopes.list_workspaces()
            if owner_type is not None
            and owner_id is not None
            and workspace.owner_type == owner_type
            and workspace.owner_id == owner_id
            and workspace.project_id in project_ids
        )
        workspace_bindings = {(workspace.project_id, workspace.id) for workspace in workspaces}

        local_models = tuple(
            model
            for model in self.models.list_models(enabled=True)
            if model.location is ModelLocation.LOCAL
        )
        self_hosted_models = tuple(
            model
            for model in self.models.list_models(enabled=True)
            if model.location is ModelLocation.SELF_HOSTED
        )
        remote_models = tuple(
            model
            for model in self.models.list_models(enabled=True)
            if model.location is ModelLocation.REMOTE
        )
        golden_path_models = (*local_models, *self_hosted_models)
        attached_provider_ids = {
            provider.descriptor.provider_id for provider in self.models.list_providers()
        }
        text_capable_models = tuple(
            model for model in golden_path_models if "text" in model.capabilities.modalities
        )
        routable_models = tuple(
            model
            for model in text_capable_models
            if model.provider_id in attached_provider_ids
            and self.models.effective_health(model) in _ROUTABLE_HEALTH
        )

        general_assistants = self._scoped_general_assistants(
            owner_type,
            owner_id,
            workspace_bindings,
        )
        executable_general_assistants: list[tuple[str, str, str]] = []
        blockers: list[JsonValue] = []
        for project_id, workspace_id, agent_id in general_assistants:
            try:
                self.preflight_general_assistant(
                    agent_id,
                    project_id=project_id,
                    workspace_id=workspace_id,
                )
            except ContractError as exc:
                blockers.append(
                    {
                        "agent_id": agent_id,
                        "project_id": project_id,
                        "workspace_id": workspace_id,
                        "error_code": exc.code.value,
                        "message": exc.message,
                    }
                )
            else:
                executable_general_assistants.append((project_id, workspace_id, agent_id))

        selected_project = projects[0] if len(projects) == 1 else None
        project_workspaces = tuple(
            workspace
            for workspace in workspaces
            if selected_project is not None and workspace.project_id == selected_project.id
        )
        selected_workspace = project_workspaces[0] if len(project_workspaces) == 1 else None
        workspace_general_assistants = tuple(
            item
            for item in general_assistants
            if selected_project is not None
            and selected_workspace is not None
            and item[0] == selected_project.id
            and item[1] == selected_workspace.id
        )
        executable_workspace_general_assistants = tuple(
            item
            for item in executable_general_assistants
            if selected_project is not None
            and selected_workspace is not None
            and item[0] == selected_project.id
            and item[1] == selected_workspace.id
        )

        selection_kind: str | None = None
        if not routable_models:
            state = "needs_model"
        elif not projects:
            state = "needs_project"
        elif len(projects) > 1:
            state = "needs_selection"
            selection_kind = "project"
        elif not project_workspaces:
            state = "needs_workspace"
        elif len(project_workspaces) > 1:
            state = "needs_selection"
            selection_kind = "workspace"
        elif not workspace_general_assistants:
            state = "needs_general_assistant"
        elif not executable_workspace_general_assistants:
            state = "needs_general_assistant"
        elif len(workspace_general_assistants) > 1:
            state = "needs_selection"
            selection_kind = "agent"
        else:
            state = "ready_for_task"

        guidance: list[JsonValue] = []
        if state == "needs_model":
            if not golden_path_models:
                guidance.append(
                    "Configure an explicit local or self-hosted ModelProvider with "
                    "onboarding.configure-model."
                )
            elif not text_capable_models:
                guidance.append(
                    "The configured local/self-hosted models do not provide the text modality "
                    "required by the first General Assistant Task; configure or enable a "
                    "text-capable canonical ModelConfiguration."
                )
            else:
                guidance.append(
                    "The configured text-capable local/self-hosted model is not currently "
                    "routable. Refresh its canonical ModelProvider health (for example with "
                    "`platform model-provider refresh-health PROVIDER_ID`) or revalidate the "
                    "endpoint before starting the first Task."
                )
            guidance.append(
                "No remote or paid provider is selected automatically and no prompt is "
                "transmitted externally by onboarding."
            )
        elif state == "needs_project":
            guidance.append("Create a canonical Project through the versioned Control Plane.")
        elif state == "needs_workspace":
            guidance.append("Create or select a canonical Workspace for the Project.")
        elif state == "needs_selection":
            guidance.append(
                f"Multiple executable first-run candidates require an explicit {selection_kind} "
                "selection. Pass the corresponding canonical project_id, workspace_id or "
                "agent_id to onboarding.run-first-task."
            )
        elif state == "needs_general_assistant":
            if workspace_general_assistants and blockers:
                guidance.append(
                    "An enabled owned General Assistant is present for the selected Workspace, "
                    "but its current editable configuration does not pass the first-run execution "
                    "preflight. Review the reported Agent blocker and its instruction, model, "
                    "capability and task-override policy."
                )
            else:
                guidance.append(
                    "Use standard-agent.bootstrap, then standard-agent.clone for "
                    "general_assistant. The editable clone must be enabled, owned by the current "
                    "user and bound to an owned Project/Workspace that can be selected by "
                    "onboarding.run-first-task."
                )
        else:
            guidance.append(
                "The first-run prerequisites are ready; start a canonical Task now or use the "
                "canonical Chat surface."
            )

        starter_catalog_installed = any(
            revision.profile.metadata.get("starter_catalog_source") == STARTER_CATALOG_SOURCE
            and revision.owner_ref.type == "service"
            for revision in (
                self.agents.get_agent_revision(definition.agent_id)
                for definition in self.agents.repository.list_agents()
            )
        )
        return {
            "id": FIRST_RUN_RESOURCE_ID,
            "type": "onboarding_status",
            "state": state,
            "authenticated_actor_present": owner_id is not None,
            "project_count": len(projects),
            "workspace_count": len(workspaces),
            "local_model_count": len(local_models),
            "self_hosted_model_count": len(self_hosted_models),
            "remote_model_count": len(remote_models),
            "text_capable_golden_path_model_count": len(text_capable_models),
            "usable_golden_path_model_count": len(routable_models),
            "general_assistant_count": len(general_assistants),
            "executable_general_assistant_count": len(executable_general_assistants),
            "general_assistant_blockers": blockers,
            "selection_required": selection_kind is not None,
            "selection_kind": selection_kind,
            "candidate_project_ids": cast(JsonValue, sorted(project_ids)),
            "candidate_workspace_ids": cast(
                JsonValue,
                sorted(workspace.id for workspace in workspaces),
            ),
            "candidate_agent_ids": cast(
                JsonValue,
                sorted(item[2] for item in general_assistants),
            ),
            "starter_catalog_installed": starter_catalog_installed,
            "installed_model_adapter_ids": cast(JsonValue, sorted(self.model_adapters)),
            "automatic_remote_provider_selection": False,
            "automatic_paid_provider_selection": False,
            "guidance": guidance,
        }

    def preflight_general_assistant(
        self,
        agent_id: str,
        *,
        project_id: str,
        workspace_id: str,
    ) -> None:
        """Validate a selected General Assistant without mutating Task/Run/Agent state."""

        preflight_first_run_agent(
            self.agent_runtime,
            agent_id,
            project_id=project_id,
            workspace_id=workspace_id,
        )

    def _scoped_general_assistants(
        self,
        owner_type: str | None,
        owner_id: str | None,
        workspace_bindings: set[tuple[str, str]],
    ) -> tuple[tuple[str, str, str], ...]:
        if owner_type is None or owner_id is None:
            return ()
        candidates: list[tuple[str, str, str]] = []
        for definition in self.agents.repository.list_agents():
            revision = self.agents.get_agent_revision(definition.agent_id)
            project_id = revision.project_id
            workspace_id = revision.workspace_id
            if (
                revision.owner_ref.type == owner_type
                and revision.owner_ref.id == owner_id
                and project_id is not None
                and workspace_id is not None
                and (project_id, workspace_id) in workspace_bindings
                and revision.profile.metadata.get("starter_key") == "general_assistant"
                and revision.profile.metadata.get("starter_catalog_source")
                == STARTER_CATALOG_SOURCE
                and revision.profile.enabled
            ):
                candidates.append((project_id, workspace_id, revision.agent_id))
        return tuple(sorted(candidates))

    def _model_adapter(self, adapter_id: str) -> OnboardingModelAdapter:
        try:
            return self.model_adapters[adapter_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.UNSUPPORTED_CAPABILITY,
                "requested first-run ModelProvider adapter is not installed",
                details={
                    "adapter_id": adapter_id,
                    "installed_adapter_ids": cast(JsonValue, sorted(self.model_adapters)),
                },
            ) from exc


def _reject_credentials(value: JsonValue, *, path: str = "payload") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            field_path = f"{path}.{key}"
            normalized = "".join(character for character in key.casefold() if character.isalnum())
            if normalized in _PLAINTEXT_CREDENTIAL_KEYS and item is not None:
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "plaintext credentials are forbidden in onboarding configuration; use a "
                    "canonical #34 SecretReference",
                    details={"field": field_path},
                )
            _reject_credentials(item, path=field_path)
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_credentials(item, path=f"{path}[{index}]")


def _optional_secret_reference(value: JsonValue | None) -> SecretReference | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "credential_ref must be a canonical SecretReference object",
        )
    provider = value.get("provider")
    secret_id = value.get("secret_id")
    scope = value.get("scope")
    version = value.get("version")
    metadata = value.get("metadata", {})
    for field_name, field_value in (
        ("provider", provider),
        ("secret_id", secret_id),
        ("scope", scope),
    ):
        if not isinstance(field_value, str) or not field_value.strip():
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"credential_ref.{field_name} must be a non-blank string",
            )
    if version is not None and (not isinstance(version, str) or not version.strip()):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "credential_ref.version must be a non-blank string when provided",
        )
    if not isinstance(metadata, dict):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "credential_ref.metadata must be a JSON object",
        )
    return SecretReference(
        provider=cast(str, provider),
        secret_id=cast(str, secret_id),
        scope=cast(str, scope),
        version=version,
        metadata=metadata,
    )


def _payload_digest(payload: dict[str, JsonValue]) -> str:
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _model_configuration_matches(
    current: ModelConfiguration,
    *,
    display_name: str,
    provider_id: str,
    aliases: tuple[str, ...],
    location: ModelLocation,
    priority: int,
    capabilities: ModelCapabilities,
    adapter_metadata: tuple[AdapterMetadata, ...],
) -> bool:
    return (
        current.display_name == display_name
        and current.provider_id == provider_id
        and current.aliases == aliases
        and current.location is location
        and current.health is HealthStatus.HEALTHY
        and current.enabled
        and current.priority == priority
        and current.capabilities == capabilities
        and current.adapter_metadata == adapter_metadata
    )


def _validated_base_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "base_url must be an explicit http(s) endpoint",
            details={"field": "base_url"},
        )
    if parsed.username is not None or parsed.password is not None:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "base_url must not embed credentials",
            details={"field": "base_url"},
        )
    if parsed.query or parsed.fragment:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "base_url must not contain query or fragment components",
            details={"field": "base_url"},
        )
    return value.rstrip("/")


def _require_loopback_endpoint(base_url: str) -> None:
    hostname = urlsplit(base_url).hostname
    if hostname is None:
        raise AssertionError("validated URL must have a hostname")
    if hostname.casefold() == "localhost":
        return
    try:
        address = ip_address(hostname)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "location='local' requires a loopback endpoint; use self_hosted for an explicitly "
            "remote self-managed endpoint",
            details={"base_url": base_url},
        ) from exc
    if not address.is_loopback:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "location='local' requires a loopback endpoint; use self_hosted for an explicitly "
            "remote self-managed endpoint",
            details={"base_url": base_url},
        )


def _golden_path_location(value: str) -> ModelLocation:
    try:
        location = ModelLocation(value)
    except ValueError as exc:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "location must be local or self_hosted for first-run model setup",
            details={"location": value},
        ) from exc
    if location is ModelLocation.REMOTE:
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "remote/paid providers are not configured by the first-run golden path; select them "
            "explicitly through normal model administration instead",
            details={"location": value},
        )
    return location


def _capabilities(payload: dict[str, JsonValue]) -> ModelCapabilities:
    raw = payload.get("capabilities")
    if raw is None:
        return ModelCapabilities(modalities=("text",))
    if not isinstance(raw, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, "capabilities must be a JSON object")
    context_window = raw.get("context_window")
    if context_window is not None and (
        isinstance(context_window, bool) or not isinstance(context_window, int)
    ):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "capabilities.context_window must be an integer",
        )
    return ModelCapabilities(
        context_window=context_window,
        tool_calling=_mapping_boolean(raw, "tool_calling"),
        structured_output=_mapping_boolean(raw, "structured_output"),
        streaming=_mapping_boolean(raw, "streaming"),
        modalities=_mapping_string_tuple(raw, "modalities", default=("text",)),
        reasoning=_mapping_string_tuple(raw, "reasoning", default=()),
    )


def _required_string(payload: dict[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value


def _optional_string(payload: dict[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value


def _optional_integer(payload: dict[str, JsonValue], key: str, *, default: int) -> int:
    value = payload.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be an integer")
    return value


def _string_tuple(payload: dict[str, JsonValue], key: str) -> tuple[str, ...]:
    raw = payload.get(key)
    if raw is None:
        return ()
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a list of strings")
    return tuple(item for item in raw if isinstance(item, str))


def _mapping_boolean(payload: dict[str, JsonValue], key: str) -> bool:
    value = payload.get(key, False)
    if not isinstance(value, bool):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"capabilities.{key} must be a boolean")
    return value


def _mapping_string_tuple(
    payload: dict[str, JsonValue],
    key: str,
    *,
    default: tuple[str, ...],
) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return default
    if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            f"capabilities.{key} must be a list of strings",
        )
    return tuple(item for item in value if isinstance(item, str))
