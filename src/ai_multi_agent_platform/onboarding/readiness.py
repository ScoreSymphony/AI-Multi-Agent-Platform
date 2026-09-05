"""Execution-compatible readiness projection for first-run onboarding."""

from __future__ import annotations

from typing import cast

from ai_multi_agent_platform.agents import STARTER_CATALOG_SOURCE
from ai_multi_agent_platform.contracts import HealthStatus
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.models import ModelLocation

from .service import FIRST_RUN_RESOURCE_ID, OnboardingService as _BaseOnboardingService

_ROUTABLE_HEALTH = frozenset({HealthStatus.HEALTHY, HealthStatus.DEGRADED})


class OnboardingService(_BaseOnboardingService):
    """First-run service whose readiness state matches the actual execution prerequisites."""

    def status(self, context: RequestContext) -> dict[str, JsonValue]:
        """Return first-run progress using the same model/Agent conditions as execution."""

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
        general_assistants = self._execution_compatible_general_assistants(
            owner_type,
            owner_id,
            workspace_bindings,
        )
        starter_catalog_installed = any(
            revision.profile.metadata.get("starter_catalog_source") == STARTER_CATALOG_SOURCE
            and revision.owner_ref.type == "service"
            for revision in (
                self.agents.get_agent_revision(definition.agent_id)
                for definition in self.agents.repository.list_agents()
            )
        )

        if not routable_models:
            state = "needs_model"
        elif not projects:
            state = "needs_project"
        elif not workspaces:
            state = "needs_workspace"
        elif not general_assistants:
            state = "needs_general_assistant"
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
        elif state == "needs_general_assistant":
            guidance.append(
                "Use standard-agent.bootstrap, then standard-agent.clone for general_assistant. "
                "The editable clone must be enabled, owned by the current user and bound to an "
                "owned Project/Workspace that can be selected by onboarding.run-first-task."
            )
        else:
            guidance.append(
                "The first-run prerequisites are ready; start a canonical Task now or use the "
                "canonical Chat surface."
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
            "starter_catalog_installed": starter_catalog_installed,
            "installed_model_adapter_ids": cast(JsonValue, sorted(self.model_adapters)),
            "automatic_remote_provider_selection": False,
            "automatic_paid_provider_selection": False,
            "guidance": guidance,
        }

    def _execution_compatible_general_assistants(
        self,
        owner_type: str | None,
        owner_id: str | None,
        workspace_bindings: set[tuple[str, str]],
    ) -> tuple[str, ...]:
        if owner_type is None or owner_id is None:
            return ()
        agent_ids: list[str] = []
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
                agent_ids.append(revision.agent_id)
        return tuple(sorted(agent_ids))
