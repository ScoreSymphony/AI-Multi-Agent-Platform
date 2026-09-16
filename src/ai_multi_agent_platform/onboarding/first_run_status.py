"""First-run readiness classification and status-document projection."""

from __future__ import annotations

from dataclasses import dataclass
from typing import cast

from ai_multi_agent_platform.contracts import HealthStatus
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.models import ModelConfiguration, ModelLocation, ModelRegistry

from .first_run_types import FirstRunPathProjection

_ROUTABLE_HEALTH = frozenset({HealthStatus.HEALTHY, HealthStatus.DEGRADED})


@dataclass(frozen=True, slots=True)
class FirstRunModelInventory:
    """Enabled model subsets relevant to first-run routing decisions."""

    local_models: tuple[ModelConfiguration, ...]
    self_hosted_models: tuple[ModelConfiguration, ...]
    remote_models: tuple[ModelConfiguration, ...]
    golden_path_models: tuple[ModelConfiguration, ...]
    text_capable_models: tuple[ModelConfiguration, ...]
    routable_models: tuple[ModelConfiguration, ...]


@dataclass(frozen=True, slots=True)
class FirstRunCandidateIds:
    """Canonical candidate identifiers exposed to an interactive first-run client."""

    project_ids: list[str]
    workspace_ids: list[str]
    agent_ids: list[str]


def collect_model_inventory(models: ModelRegistry) -> FirstRunModelInventory:
    """Collect enabled model subsets and current routability in one read phase."""

    enabled_models = models.list_models(enabled=True)
    local_models = tuple(
        model for model in enabled_models if model.location is ModelLocation.LOCAL
    )
    self_hosted_models = tuple(
        model for model in enabled_models if model.location is ModelLocation.SELF_HOSTED
    )
    remote_models = tuple(
        model for model in enabled_models if model.location is ModelLocation.REMOTE
    )
    golden_path_models = (*local_models, *self_hosted_models)
    attached_provider_ids = {
        provider.descriptor.provider_id for provider in models.list_providers()
    }
    text_capable_models = tuple(
        model for model in golden_path_models if "text" in model.capabilities.modalities
    )
    routable_models = tuple(
        model
        for model in text_capable_models
        if model.provider_id in attached_provider_ids
        and models.effective_health(model) in _ROUTABLE_HEALTH
    )
    return FirstRunModelInventory(
        local_models=local_models,
        self_hosted_models=self_hosted_models,
        remote_models=remote_models,
        golden_path_models=golden_path_models,
        text_capable_models=text_capable_models,
        routable_models=routable_models,
    )


def classify_first_run_state(
    projection: FirstRunPathProjection,
    inventory: FirstRunModelInventory,
) -> tuple[str, str | None]:
    """Classify the next onboarding action and any required explicit selection kind."""

    if not inventory.routable_models:
        return "needs_model", None
    if not projection.project_ids:
        return "needs_project", None
    if projection.executable_paths:
        selection_kind = first_run_selection_kind(projection.executable_paths)
        if selection_kind is not None:
            return "needs_selection", selection_kind
        return "ready_for_task", None
    if not projection.workspace_bindings:
        return "needs_workspace", None
    return "needs_general_assistant", None


def first_run_guidance(
    state: str,
    *,
    selection_kind: str | None,
    projection: FirstRunPathProjection,
    inventory: FirstRunModelInventory,
) -> list[JsonValue]:
    """Return stable actionable guidance for one classified first-run state."""

    if state == "needs_model":
        return _model_guidance(inventory)
    if state == "needs_project":
        return ["Create a canonical Project through the versioned Control Plane."]
    if state == "needs_workspace":
        return ["Create a canonical Workspace for an owned Project."]
    if state == "needs_selection":
        return [
            f"Multiple executable first-run candidates require an explicit {selection_kind} "
            "selection. Pass the corresponding canonical project_id, workspace_id or "
            "agent_id to onboarding.run-first-task."
        ]
    if state == "needs_general_assistant":
        if projection.structural_paths and projection.blockers:
            return [
                "Enabled owned General Assistants are present, but their current editable "
                "configurations do not pass the first-run execution preflight. Review the "
                "reported Agent blockers and their instruction, model, capability and "
                "task-override policies."
            ]
        return [
            "Use standard-agent.bootstrap, then standard-agent.clone for general_assistant. "
            "The editable clone must be enabled, owned by the current user and bound to an "
            "owned Project/Workspace."
        ]
    return [
        "The first-run prerequisites are ready; start a canonical Task now or use the "
        "canonical Chat surface."
    ]


def candidate_ids(projection: FirstRunPathProjection) -> FirstRunCandidateIds:
    """Project deterministic candidate identifiers from executable or structural paths."""

    paths = projection.executable_paths or projection.structural_paths
    if paths:
        project_ids = sorted({path.project_id for path in paths})
        workspace_ids = sorted({path.workspace_id for path in paths})
    else:
        project_ids = list(projection.project_ids)
        workspace_ids = sorted(workspace_id for _, workspace_id in projection.workspace_bindings)
    return FirstRunCandidateIds(
        project_ids=project_ids,
        workspace_ids=workspace_ids,
        agent_ids=sorted({path.agent_id for path in paths}),
    )


def build_status_document(
    *,
    authenticated_actor_present: bool,
    project_count: int,
    workspace_count: int,
    projection: FirstRunPathProjection,
    inventory: FirstRunModelInventory,
    state: str,
    selection_kind: str | None,
    candidates: FirstRunCandidateIds,
    starter_catalog_installed: bool,
    installed_model_adapter_ids: list[str],
    guidance: list[JsonValue],
) -> dict[str, JsonValue]:
    """Build the stable public onboarding status document."""

    return {
        "id": "first-run",
        "type": "onboarding_status",
        "state": state,
        "authenticated_actor_present": authenticated_actor_present,
        "project_count": project_count,
        "workspace_count": workspace_count,
        "local_model_count": len(inventory.local_models),
        "self_hosted_model_count": len(inventory.self_hosted_models),
        "remote_model_count": len(inventory.remote_models),
        "text_capable_golden_path_model_count": len(inventory.text_capable_models),
        "usable_golden_path_model_count": len(inventory.routable_models),
        "general_assistant_count": len(projection.structural_paths),
        "executable_general_assistant_count": len(projection.executable_paths),
        "general_assistant_blockers": cast(JsonValue, list(projection.blockers)),
        "selection_required": selection_kind is not None,
        "selection_kind": selection_kind,
        "candidate_project_ids": cast(JsonValue, candidates.project_ids),
        "candidate_workspace_ids": cast(JsonValue, candidates.workspace_ids),
        "candidate_agent_ids": cast(JsonValue, candidates.agent_ids),
        "starter_catalog_installed": starter_catalog_installed,
        "installed_model_adapter_ids": cast(JsonValue, installed_model_adapter_ids),
        "automatic_remote_provider_selection": False,
        "automatic_paid_provider_selection": False,
        "guidance": guidance,
    }


def first_run_selection_kind(paths: tuple[object, ...]) -> str | None:
    """Return the first canonical identity dimension that remains ambiguous."""

    project_ids = {getattr(path, "project_id") for path in paths}
    if len(project_ids) > 1:
        return "project"
    workspace_ids = {getattr(path, "workspace_id") for path in paths}
    if len(workspace_ids) > 1:
        return "workspace"
    agent_ids = {getattr(path, "agent_id") for path in paths}
    if len(agent_ids) > 1:
        return "agent"
    return None


def _model_guidance(inventory: FirstRunModelInventory) -> list[JsonValue]:
    guidance: list[JsonValue] = []
    if not inventory.golden_path_models:
        guidance.append(
            "Configure an explicit local or self-hosted ModelProvider with onboarding.configure-model."
        )
    elif not inventory.text_capable_models:
        guidance.append(
            "The configured local/self-hosted models do not provide the text modality required by "
            "the first General Assistant Task; configure or enable a text-capable canonical "
            "ModelConfiguration."
        )
    else:
        guidance.append(
            "The configured text-capable local/self-hosted model is not currently routable. "
            "Refresh its canonical ModelProvider health (for example with `platform model-provider "
            "refresh-health PROVIDER_ID`) or revalidate the endpoint before starting the first Task."
        )
    guidance.append(
        "No remote or paid provider is selected automatically and no prompt is transmitted "
        "externally by onboarding."
    )
    return guidance
