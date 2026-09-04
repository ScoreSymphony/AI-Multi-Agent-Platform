"""Single-node subsystem validators registered with the generic restore integrity gate."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ai_multi_agent_platform.backup.integrity import (
    RestoreIntegrityContext,
    RestoreIntegrityValidator,
    RestoreValidationError,
)
from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.conversations import AgentSelectionRef, ParticipantKind, ReferenceKind

if TYPE_CHECKING:
    from .single_node import SingleNodeDeployment


def single_node_restore_integrity_validators(
    deployment: SingleNodeDeployment,
) -> tuple[RestoreIntegrityValidator, ...]:
    """Return validators for durable subsystems composed by ``SingleNodeDeployment``."""

    async def agents(context: RestoreIntegrityContext) -> str:
        return await _validate_agents(deployment, context)

    async def conversations(context: RestoreIntegrityContext) -> str:
        return await _validate_conversations(deployment, context)

    return (agents, conversations)


async def _reference_sets(
    context: RestoreIntegrityContext,
) -> tuple[set[str], dict[str, str], set[str], set[str]]:
    task_ids: set[str] = set()
    run_to_task: dict[str, str] = {}
    artifact_ids: set[str] = set()
    result_ids: set[str] = set()
    for report in context.reports:
        task = await context.kernel.get_task(report.task_id)
        task_ids.add(task.task_id)
        artifact_ids.update(task.artifact_ids)
        result_ids.update(task.result_ids)
        for run_id in task.run_ids:
            run = await context.kernel.get_run(task.task_id, run_id)
            run_to_task[run_id] = task.task_id
            artifact_ids.update(run.artifact_ids)
            result_ids.update(run.result_ids)
    return task_ids, run_to_task, artifact_ids, result_ids


def _validate_scope_reference(
    context: RestoreIntegrityContext,
    *,
    owner: str,
    project_id: str | None,
    workspace_id: str | None,
) -> None:
    if project_id is not None:
        try:
            context.scopes.get_project(project_id)
        except ContractError as exc:
            raise RestoreValidationError(
                f"{owner} references missing project {project_id}"
            ) from exc
    if workspace_id is not None:
        try:
            workspace = context.scopes.get_workspace(workspace_id)
        except ContractError as exc:
            raise RestoreValidationError(
                f"{owner} references missing workspace {workspace_id}"
            ) from exc
        if project_id is not None and workspace.project_id != project_id:
            raise RestoreValidationError(
                f"{owner} workspace {workspace_id} belongs to project "
                f"{workspace.project_id}, expected {project_id}"
            )


def _require_model(deployment: SingleNodeDeployment, model_config_id: str, owner: str) -> None:
    try:
        deployment.models.get_model(model_config_id)
    except (ContractError, KeyError, ValueError) as exc:
        raise RestoreValidationError(
            f"{owner} references missing model configuration {model_config_id}"
        ) from exc


async def _validate_agents(
    deployment: SingleNodeDeployment,
    context: RestoreIntegrityContext,
) -> str:
    repository = deployment.agents.repository
    task_ids, run_to_task, artifact_ids, result_ids = await _reference_sets(context)

    agent_ids = {agent.agent_id for agent in repository.list_agents()}
    team_ids = {team.team_id for team in repository.list_teams()}

    for agent in repository.list_agents():
        _validate_scope_reference(
            context,
            owner=f"agent {agent.agent_id}",
            project_id=agent.project_id,
            workspace_id=agent.workspace_id,
        )
        revision = repository.get_agent_revision(agent.agent_id, agent.current_revision)
        _validate_scope_reference(
            context,
            owner=f"agent revision {agent.agent_id}@{revision.revision}",
            project_id=revision.project_id,
            workspace_id=revision.workspace_id,
        )
        defaults = revision.profile.workspace_defaults
        _validate_scope_reference(
            context,
            owner=f"agent workspace defaults {agent.agent_id}@{revision.revision}",
            project_id=defaults.project_id,
            workspace_id=defaults.workspace_id,
        )

    for team in repository.list_teams():
        _validate_scope_reference(
            context,
            owner=f"agent team {team.team_id}",
            project_id=team.project_id,
            workspace_id=team.workspace_id,
        )
        revision = repository.get_team_revision(team.team_id, team.current_revision)
        _validate_scope_reference(
            context,
            owner=f"agent team revision {team.team_id}@{revision.revision}",
            project_id=revision.project_id,
            workspace_id=revision.workspace_id,
        )
        for member in revision.profile.members:
            if member.agent.agent_id not in agent_ids:
                raise RestoreValidationError(
                    f"agent team {team.team_id} references missing agent {member.agent.agent_id}"
                )
            repository.get_agent_revision(member.agent.agent_id, member.agent.revision)

    for record in repository.list_agent_runs():
        if record.task_id not in task_ids:
            raise RestoreValidationError(
                f"agent run {record.agent_run_id} references missing task {record.task_id}"
            )
        if run_to_task.get(record.run_id) != record.task_id:
            raise RestoreValidationError(
                f"agent run {record.agent_run_id} references missing/mismatched run {record.run_id}"
            )
        repository.get_agent_revision(record.agent.agent_id, record.agent.revision)
        if record.team is not None:
            if record.team.team_id not in team_ids:
                raise RestoreValidationError(
                    f"agent run {record.agent_run_id} references missing team {record.team.team_id}"
                )
            repository.get_team_revision(record.team.team_id, record.team.revision)
        if record.selected_model_config_id is not None:
            _require_model(
                deployment,
                record.selected_model_config_id,
                f"agent run {record.agent_run_id}",
            )
        missing_artifacts = set(record.artifact_ids) - artifact_ids
        if missing_artifacts:
            raise RestoreValidationError(
                f"agent run {record.agent_run_id} references missing artifacts "
                f"{sorted(missing_artifacts)!r}"
            )
        missing_results = set(record.result_ids) - result_ids
        if missing_results:
            raise RestoreValidationError(
                f"agent run {record.agent_run_id} references missing results "
                f"{sorted(missing_results)!r}"
            )

    return (
        "agent-team-run-references:"
        f"{len(agent_ids)}:{len(team_ids)}:{len(repository.list_agent_runs())}"
    )


def _validate_agent_selection(
    deployment: SingleNodeDeployment,
    selection: AgentSelectionRef,
    owner: str,
) -> None:
    repository = deployment.agents.repository
    try:
        if selection.kind is ParticipantKind.AGENT:
            definition = repository.get_agent(selection.id)
            revision = selection.revision or definition.current_revision
            repository.get_agent_revision(selection.id, revision)
        elif selection.kind is ParticipantKind.AGENT_TEAM:
            definition = repository.get_team(selection.id)
            revision = selection.revision or definition.current_revision
            repository.get_team_revision(selection.id, revision)
    except ContractError as exc:
        raise RestoreValidationError(
            f"{owner} references missing {selection.kind.value} {selection.id}"
        ) from exc


async def _validate_conversations(
    deployment: SingleNodeDeployment,
    context: RestoreIntegrityContext,
) -> str:
    task_ids, run_to_task, artifact_ids, result_ids = await _reference_sets(context)
    conversations = await deployment.conversations.list_conversations(include_archived=True)
    message_count = 0

    for conversation in conversations:
        _validate_scope_reference(
            context,
            owner=f"conversation {conversation.id}",
            project_id=conversation.project_id,
            workspace_id=conversation.workspace_id,
        )
        for participant in conversation.participants:
            if participant.kind is ParticipantKind.AGENT:
                try:
                    deployment.agents.repository.get_agent(participant.id)
                except ContractError as exc:
                    raise RestoreValidationError(
                        f"conversation {conversation.id} references missing agent {participant.id}"
                    ) from exc
            elif participant.kind is ParticipantKind.AGENT_TEAM:
                try:
                    deployment.agents.repository.get_team(participant.id)
                except ContractError as exc:
                    raise RestoreValidationError(
                        f"conversation {conversation.id} references missing team {participant.id}"
                    ) from exc
        if conversation.default_agent is not None:
            _validate_agent_selection(
                deployment,
                conversation.default_agent,
                f"conversation {conversation.id}",
            )
        if (
            conversation.model_preference is not None
            and conversation.model_preference.model_config_id is not None
        ):
            _require_model(
                deployment,
                conversation.model_preference.model_config_id,
                f"conversation {conversation.id}",
            )
        missing_tasks = set(conversation.task_ids) - task_ids
        if missing_tasks:
            raise RestoreValidationError(
                f"conversation {conversation.id} references missing tasks {sorted(missing_tasks)!r}"
            )
        missing_runs = set(conversation.run_ids) - set(run_to_task)
        if missing_runs:
            raise RestoreValidationError(
                f"conversation {conversation.id} references missing runs {sorted(missing_runs)!r}"
            )
        missing_artifacts = set(conversation.artifact_ids) - artifact_ids
        if missing_artifacts:
            raise RestoreValidationError(
                f"conversation {conversation.id} references missing artifacts "
                f"{sorted(missing_artifacts)!r}"
            )

        cursor: str | None = None
        while True:
            messages, cursor = await deployment.conversations.list_messages(
                conversation.id,
                limit=200,
                cursor=cursor,
            )
            for message in messages:
                message_count += 1
                if message.model_config_id is not None:
                    _require_model(
                        deployment,
                        message.model_config_id,
                        f"conversation message {message.id}",
                    )
                references = list(message.references)
                for block in message.content:
                    if block.reference is not None:
                        references.append(block.reference)
                for reference in references:
                    if reference.kind is ReferenceKind.TASK and reference.id not in task_ids:
                        raise RestoreValidationError(
                            f"conversation message {message.id} references missing task {reference.id}"
                        )
                    if reference.kind is ReferenceKind.RUN and reference.id not in run_to_task:
                        raise RestoreValidationError(
                            f"conversation message {message.id} references missing run {reference.id}"
                        )
                    if (
                        reference.kind is ReferenceKind.ARTIFACT
                        and reference.id not in artifact_ids
                    ):
                        raise RestoreValidationError(
                            f"conversation message {message.id} references missing artifact {reference.id}"
                        )
                    if reference.kind is ReferenceKind.RESULT and reference.id not in result_ids:
                        raise RestoreValidationError(
                            f"conversation message {message.id} references missing result {reference.id}"
                        )
                    if reference.kind is ReferenceKind.AGENT:
                        try:
                            deployment.agents.repository.get_agent(reference.id)
                        except ContractError as exc:
                            raise RestoreValidationError(
                                f"conversation message {message.id} references missing agent "
                                f"{reference.id}"
                            ) from exc
                    if reference.kind is ReferenceKind.AGENT_TEAM:
                        try:
                            deployment.agents.repository.get_team(reference.id)
                        except ContractError as exc:
                            raise RestoreValidationError(
                                f"conversation message {message.id} references missing team "
                                f"{reference.id}"
                            ) from exc
            if cursor is None:
                break

    return f"conversation-message-references:{len(conversations)}:{message_count}"
