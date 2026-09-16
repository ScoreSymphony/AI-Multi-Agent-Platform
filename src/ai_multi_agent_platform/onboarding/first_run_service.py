"""First-run onboarding state and local/self-hosted model golden-path service."""

from __future__ import annotations

from collections.abc import Iterable

from ai_multi_agent_platform.agents.runtime import AgentRuntime
from ai_multi_agent_platform.agents.service import AgentService
from ai_multi_agent_platform.agents.standards import STARTER_CATALOG_SOURCE
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.control_plane.service import ScopeStore
from ai_multi_agent_platform.models import JsonModelRegistryStore, ModelRegistry

from .agent_lifecycle import preflight_first_run_agent
from .first_run_model_setup import FIRST_RUN_RESOURCE_ID as FIRST_RUN_RESOURCE_ID
from .first_run_model_setup import ONBOARDING_COLLECTION as ONBOARDING_COLLECTION
from .first_run_model_setup import ONBOARDING_COMMANDS as ONBOARDING_COMMANDS
from .first_run_model_setup import (
    ONBOARDING_CONFIGURE_MODEL_COMMAND as ONBOARDING_CONFIGURE_MODEL_COMMAND,
)
from .first_run_model_setup import (
    apply_model_setup,
    build_model_setup_plan,
    build_provider_from_record,
    model_setup_result,
    persist_model_setup,
    record_configure_command,
    validate_configure_command,
    validate_model_endpoint,
)
from .first_run_resolution import resolve_first_run_path as resolve_projected_first_run_path
from .first_run_status import (
    build_status_document,
    candidate_ids,
    classify_first_run_state,
    collect_model_inventory,
    first_run_guidance,
)
from .first_run_types import FirstRunPath as FirstRunPath
from .first_run_types import FirstRunPathProjection as FirstRunPathProjection
from .persistence import (
    JsonModelProviderSetupStore,
    JsonOnboardingCommandStore,
    ModelProviderSetupRecord,
    OnboardingCommandRecord,
)
from .providers import OnboardingModelAdapter


class OnboardingService:
    """Compose canonical subsystems into one explainable first-run path.

    This service does not own Project, Workspace, Agent or Task lifecycles. It coordinates
    focused first-run model setup, readiness projection and path-resolution seams around the
    canonical subsystem owners.
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
            provider = build_provider_from_record(provider_record, self.model_adapters)
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

        idempotency_key, payload_digest, replay = validate_configure_command(
            context,
            resource_ref,
            payload,
            self._command_records,
        )
        if replay is not None:
            return replay

        plan = build_model_setup_plan(
            payload,
            model_adapters=self.model_adapters,
            provider_records=self._provider_records,
        )
        await validate_model_endpoint(plan, model_adapters=self.model_adapters)
        apply_model_setup(self.models, plan)
        persist_model_setup(
            plan,
            models=self.models,
            model_store=self.model_store,
            provider_store=self.provider_store,
            provider_records=self._provider_records,
        )
        result = model_setup_result(plan)
        record_configure_command(
            context,
            resource_ref=resource_ref,
            idempotency_key=idempotency_key,
            payload_digest=payload_digest,
            result=result,
            command_records=self._command_records,
            command_store=self.command_store,
        )
        return result

    def status(self, context: RequestContext) -> dict[str, JsonValue]:
        """Return first-run progress using the same executable paths as first-Task resolution."""

        projection = self.first_run_path_projection(context)
        project_count = sum(
            1 for project in self.scopes.list_projects() if project.id in projection.project_ids
        )
        workspace_count = sum(
            1
            for workspace in self.scopes.list_workspaces()
            if (workspace.project_id, workspace.id) in projection.workspace_bindings
        )
        inventory = collect_model_inventory(self.models)
        state, selection_kind = classify_first_run_state(projection, inventory)
        guidance = first_run_guidance(
            state,
            selection_kind=selection_kind,
            projection=projection,
            inventory=inventory,
        )
        return build_status_document(
            authenticated_actor_present=context.actor.owner_id is not None,
            project_count=project_count,
            workspace_count=workspace_count,
            projection=projection,
            inventory=inventory,
            state=state,
            selection_kind=selection_kind,
            candidates=candidate_ids(projection),
            starter_catalog_installed=self._starter_catalog_installed(),
            installed_model_adapter_ids=sorted(self.model_adapters),
            guidance=guidance,
        )

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

        if context.actor.owner_type is None or context.actor.owner_id is None:
            raise ContractError(
                ErrorCode.UNAUTHORIZED,
                "first-run Task requires an authenticated canonical owner",
            )
        projection = self.first_run_path_projection(context)
        return resolve_projected_first_run_path(
            projection,
            project_id=project_id,
            workspace_id=workspace_id,
            agent_id=agent_id,
            preflight=self._preflight_path,
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

    def _preflight_path(self, path: FirstRunPath) -> None:
        self.preflight_general_assistant(
            path.agent_id,
            project_id=path.project_id,
            workspace_id=path.workspace_id,
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

    def _starter_catalog_installed(self) -> bool:
        return any(
            revision.profile.metadata.get("starter_catalog_source") == STARTER_CATALOG_SOURCE
            and revision.owner_ref.type == "service"
            for revision in (
                self.agents.get_agent_revision(definition.agent_id)
                for definition in self.agents.repository.list_agents()
            )
        )
