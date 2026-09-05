"""First-run onboarding state and local/self-hosted model golden-path service."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from dataclasses import dataclass, replace
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


@dataclass(frozen=True, slots=True)
class FirstRunPath:
    """One canonical Project/Workspace/General-Assistant execution path."""

    project_id: str
    workspace_id: str
    agent_id: str


@dataclass(frozen=True, slots=True)
class FirstRunPathProjection:
    """Structural and executable first-run paths derived without side effects."""

    project_ids: tuple[str, ...]
    workspace_bindings: tuple[tuple[str, str], ...]
    structural_paths: tuple[FirstRunPath, ...]
    executable_paths: tuple[FirstRunPath, ...]
    blockers: tuple[dict[str, JsonValue], ...]


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
        """Return first-run progress using the same executable paths as first-Task resolution."""

        projection = self.first_run_path_projection(context)
        projects = tuple(
            project
            for project in self.scopes.list_projects()
            if project.id in projection.project_ids
        )
        workspaces = tuple(
            workspace
            for workspace in self.scopes.list_workspaces()
            if (workspace.project_id, workspace.id) in projection.workspace_bindings
        )

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

        selection_kind: str | None = None
        if not routable_models:
            state = "needs_model"
        elif not projection.project_ids:
            state = "needs_project"
        elif projection.executable_paths:
            selection_kind = _first_run_selection_kind(projection.executable_paths)
            state = "needs_selection" if selection_kind is not None else "ready_for_task"
        elif not projection.workspace_bindings:
            state = "needs_workspace"
        else:
            state = "needs_general_assistant"

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
            guidance.append("Create a canonical Workspace for an owned Project.")
        elif state == "needs_selection":
            guidance.append(
                f"Multiple executable first-run candidates require an explicit {selection_kind} "
                "selection. Pass the corresponding canonical project_id, workspace_id or "
                "agent_id to onboarding.run-first-task."
            )
        elif state == "needs_general_assistant":
            if projection.structural_paths and projection.blockers:
                guidance.append(
                    "Enabled owned General Assistants are present, but their current editable "
                    "configurations do not pass the first-run execution preflight. Review the "
                    "reported Agent blockers and their instruction, model, capability and "
                    "task-override policies."
                )
            else:
                guidance.append(
                    "Use standard-agent.bootstrap, then standard-agent.clone for "
                    "general_assistant. The editable clone must be enabled, owned by the current "
                    "user and bound to an owned Project/Workspace."
                )
        else:
            guidance.append(
                "The first-run prerequisites are ready; start a canonical Task now or use the "
                "canonical Chat surface."
            )

        candidate_paths = (
            projection.executable_paths
            if projection.executable_paths
            else projection.structural_paths
        )
        candidate_project_ids = (
            sorted({path.project_id for path in candidate_paths})
            if candidate_paths
            else list(projection.project_ids)
        )
        candidate_workspace_ids = (
            sorted({path.workspace_id for path in candidate_paths})
            if candidate_paths
            else sorted(workspace_id for _, workspace_id in projection.workspace_bindings)
        )
        candidate_agent_ids = sorted({path.agent_id for path in candidate_paths})

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
            "authenticated_actor_present": context.actor.owner_id is not None,
            "project_count": len(projects),
            "workspace_count": len(workspaces),
            "local_model_count": len(local_models),
            "self_hosted_model_count": len(self_hosted_models),
            "remote_model_count": len(remote_models),
            "text_capable_golden_path_model_count": len(text_capable_models),
            "usable_golden_path_model_count": len(routable_models),
            "general_assistant_count": len(projection.structural_paths),
            "executable_general_assistant_count": len(projection.executable_paths),
            "general_assistant_blockers": cast(JsonValue, list(projection.blockers)),
            "selection_required": selection_kind is not None,
            "selection_kind": selection_kind,
            "candidate_project_ids": cast(JsonValue, candidate_project_ids),
            "candidate_workspace_ids": cast(JsonValue, candidate_workspace_ids),
            "candidate_agent_ids": cast(JsonValue, candidate_agent_ids),
            "starter_catalog_installed": starter_catalog_installed,
            "installed_model_adapter_ids": cast(JsonValue, sorted(self.model_adapters)),
            "automatic_remote_provider_selection": False,
            "automatic_paid_provider_selection": False,
            "guidance": guidance,
        }

    def first_run_path_projection(self, context: RequestContext) -> FirstRunPathProjection:
        """Project structural and executable first-run paths without mutating canonical state."""

        owner_type = context.actor.owner_type
        owner_id = context.actor.owner_id
        if owner_type is None or owner_id is None:
            return FirstRunPathProjection((), (), (), (), ())

        project_ids = tuple(
            sorted(
                project.id
                for project in self.scopes.list_projects()
                if project.owner_ref.type == owner_type and project.owner_ref.id == owner_id
            )
        )
        owned_projects = set(project_ids)
        workspace_bindings = tuple(
            sorted(
                (workspace.project_id, workspace.id)
                for workspace in self.scopes.list_workspaces()
                if workspace.owner_type == owner_type
                and workspace.owner_id == owner_id
                and workspace.project_id in owned_projects
            )
        )
        structural_paths = tuple(
            FirstRunPath(project_id, workspace_id, agent_id)
            for project_id, workspace_id, agent_id in self._scoped_general_assistants(
                owner_type,
                owner_id,
                set(workspace_bindings),
            )
        )
        executable_paths: list[FirstRunPath] = []
        blockers: list[dict[str, JsonValue]] = []
        for path in structural_paths:
            try:
                self.preflight_general_assistant(
                    path.agent_id,
                    project_id=path.project_id,
                    workspace_id=path.workspace_id,
                )
            except ContractError as exc:
                blockers.append(
                    {
                        "agent_id": path.agent_id,
                        "project_id": path.project_id,
                        "workspace_id": path.workspace_id,
                        "error_code": exc.code.value,
                        "message": exc.message,
                    }
                )
            else:
                executable_paths.append(path)
        return FirstRunPathProjection(
            project_ids=project_ids,
            workspace_bindings=workspace_bindings,
            structural_paths=structural_paths,
            executable_paths=tuple(executable_paths),
            blockers=tuple(blockers),
        )

    def resolve_first_run_path(
        self,
        context: RequestContext,
        *,
        project_id: str | None = None,
        workspace_id: str | None = None,
        agent_id: str | None = None,
    ) -> FirstRunPath:
        """Resolve exactly one executable path, respecting any explicit canonical IDs."""

        owner_type = context.actor.owner_type
        owner_id = context.actor.owner_id
        if owner_type is None or owner_id is None:
            raise ContractError(
                ErrorCode.UNAUTHORIZED,
                "first-run Task requires an authenticated canonical owner",
            )

        projection = self.first_run_path_projection(context)
        if project_id is not None and project_id not in projection.project_ids:
            raise ContractError(ErrorCode.FORBIDDEN, "Project is not owned by the caller")
        if workspace_id is not None:
            matching_workspaces = {
                candidate_workspace_id
                for candidate_project_id, candidate_workspace_id in projection.workspace_bindings
                if project_id is None or candidate_project_id == project_id
            }
            if workspace_id not in matching_workspaces:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "Workspace is not owned by the caller or does not belong to the selected "
                    "Project",
                )
        if agent_id is not None:
            structural_agent_paths = _filter_first_run_paths(
                projection.structural_paths,
                project_id=project_id,
                workspace_id=workspace_id,
                agent_id=agent_id,
            )
            if not structural_agent_paths:
                raise ContractError(
                    ErrorCode.FORBIDDEN,
                    "selected Agent is not an enabled owned General Assistant for the selected "
                    "Project/Workspace",
                )

        executable = _filter_first_run_paths(
            projection.executable_paths,
            project_id=project_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
        )
        if len(executable) == 1:
            return executable[0]
        if len(executable) > 1:
            selection_kind = _first_run_selection_kind(executable)
            assert selection_kind is not None
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                f"{selection_kind}_id is required because multiple executable first-run paths "
                "remain",
                details={
                    "selection_kind": selection_kind,
                    "candidate_project_ids": cast(
                        JsonValue, sorted({path.project_id for path in executable})
                    ),
                    "candidate_workspace_ids": cast(
                        JsonValue, sorted({path.workspace_id for path in executable})
                    ),
                    "candidate_agent_ids": cast(
                        JsonValue, sorted({path.agent_id for path in executable})
                    ),
                },
            )

        structural = _filter_first_run_paths(
            projection.structural_paths,
            project_id=project_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
        )
        if len(structural) == 1:
            blocked = structural[0]
            self.preflight_general_assistant(
                blocked.agent_id,
                project_id=blocked.project_id,
                workspace_id=blocked.workspace_id,
            )
            raise AssertionError("first-run preflight unexpectedly accepted a blocked path")

        if not projection.project_ids:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "first-run onboarding requires an owned Project",
            )
        matching_projects = {project_id} if project_id is not None else set(projection.project_ids)
        matching_workspace_bindings = tuple(
            binding for binding in projection.workspace_bindings if binding[0] in matching_projects
        )
        if not matching_workspace_bindings:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "first-run onboarding requires a Workspace for the selected Project",
            )
        if workspace_id is not None and not structural:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "selected Workspace has no executable enabled owned General Assistant",
            )
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "no executable first-run path matches the current selection; review General Assistant "
            "preflight blockers",
            details={"general_assistant_blockers": cast(JsonValue, list(projection.blockers))},
        )

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


def _filter_first_run_paths(
    paths: tuple[FirstRunPath, ...],
    *,
    project_id: str | None,
    workspace_id: str | None,
    agent_id: str | None,
) -> tuple[FirstRunPath, ...]:
    return tuple(
        path
        for path in paths
        if (project_id is None or path.project_id == project_id)
        and (workspace_id is None or path.workspace_id == workspace_id)
        and (agent_id is None or path.agent_id == agent_id)
    )


def _first_run_selection_kind(paths: tuple[FirstRunPath, ...]) -> str | None:
    if len({path.project_id for path in paths}) > 1:
        return "project"
    if len({path.workspace_id for path in paths}) > 1:
        return "workspace"
    if len({path.agent_id for path in paths}) > 1:
        return "agent"
    return None


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
