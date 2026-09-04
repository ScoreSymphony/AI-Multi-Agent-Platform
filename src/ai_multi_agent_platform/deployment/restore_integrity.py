"""Single-node application-store validators for the generic restore readiness gate."""

from __future__ import annotations

import sqlite3
from collections.abc import Awaitable, Callable

from ai_multi_agent_platform.backup.integrity import RestoreValidationError
from ai_multi_agent_platform.conversations import JsonConversationRepository
from ai_multi_agent_platform.conversations.models import (
    ParticipantKind,
    ReferenceKind,
    ResourceReference,
)
from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.kernel import RecoveryReport

from .single_node import SingleNodeDeployment

DeploymentRestoreValidator = Callable[[tuple[RecoveryReport, ...]], Awaitable[tuple[str, ...]]]


def single_node_restore_integrity_validators(
    deployment: SingleNodeDeployment,
) -> tuple[DeploymentRestoreValidator, ...]:
    """Return validators for durable subsystems composed by ``SingleNodeDeployment``."""

    async def agents(reports: tuple[RecoveryReport, ...]) -> tuple[str, ...]:
        count = await _validate_agents(deployment, reports)
        return (f"agent-team-run-references:{count}",)

    async def conversations(reports: tuple[RecoveryReport, ...]) -> tuple[str, ...]:
        count, message_count = await _validate_conversations(deployment, reports)
        return (f"conversation-message-references:{count}:{message_count}",)

    return (agents, conversations)


async def _validate_agents(
    deployment: SingleNodeDeployment,
    reports: tuple[RecoveryReport, ...],
) -> int:
    repository = deployment.agents.repository
    checked = 0

    for definition in repository.list_agents():
        _validate_scope(
            deployment,
            entity=f"agent {definition.agent_id}",
            project_id=definition.project_id,
            workspace_id=definition.workspace_id,
        )
        for revision in repository.list_agent_revisions(definition.agent_id):
            _validate_scope(
                deployment,
                entity=f"agent {definition.agent_id}@{revision.revision}",
                project_id=revision.project_id,
                workspace_id=revision.workspace_id,
            )
            checked += 1

    for definition in repository.list_teams():
        _validate_scope(
            deployment,
            entity=f"agent team {definition.team_id}",
            project_id=definition.project_id,
            workspace_id=definition.workspace_id,
        )
        for revision in repository.list_team_revisions(definition.team_id):
            _validate_scope(
                deployment,
                entity=f"agent team {definition.team_id}@{revision.revision}",
                project_id=revision.project_id,
                workspace_id=revision.workspace_id,
            )
            for member in revision.profile.members:
                _require_agent_revision(
                    deployment,
                    member.agent.agent_id,
                    member.agent.revision,
                    entity=f"agent team {definition.team_id}@{revision.revision}",
                )
            checked += 1

    known_tasks = {report.task_id for report in reports}
    for record in repository.list_agent_runs():
        if record.task_id not in known_tasks:
            raise RestoreValidationError(
                f"agent run {record.agent_run_id} references missing task {record.task_id}"
            )
        try:
            task = await deployment.kernel.get_task(record.task_id)
            run = await deployment.kernel.get_run(record.task_id, record.run_id)
        except ContractError as exc:
            raise RestoreValidationError(
                f"agent run {record.agent_run_id} references missing canonical run {record.run_id}"
            ) from exc
        _require_agent_revision(
            deployment,
            record.agent.agent_id,
            record.agent.revision,
            entity=f"agent run {record.agent_run_id}",
        )
        if record.team is not None:
            try:
                repository.get_team_revision(record.team.team_id, record.team.revision)
            except ContractError as exc:
                raise RestoreValidationError(
                    f"agent run {record.agent_run_id} references missing team revision "
                    f"{record.team.team_id}@{record.team.revision}"
                ) from exc
        if record.selected_model_config_id is not None:
            _require_model(
                deployment, record.selected_model_config_id, f"agent run {record.agent_run_id}"
            )
        known_artifacts = set(task.artifact_ids) | set(run.artifact_ids)
        missing_artifacts = set(record.artifact_ids) - known_artifacts
        if missing_artifacts:
            raise RestoreValidationError(
                f"agent run {record.agent_run_id} references unattached artifacts "
                f"{sorted(missing_artifacts)!r}"
            )
        known_results = set(task.result_ids) | set(run.result_ids)
        missing_results = set(record.result_ids) - known_results
        if missing_results:
            raise RestoreValidationError(
                f"agent run {record.agent_run_id} references unattached results "
                f"{sorted(missing_results)!r}"
            )
        checked += 1

    return checked


async def _validate_conversations(
    deployment: SingleNodeDeployment,
    reports: tuple[RecoveryReport, ...],
) -> tuple[int, int]:
    path = deployment.config.database_dir / "conversations.json"
    if not path.is_file():
        return 0, 0

    repository = JsonConversationRepository(path)
    conversations = await repository.list_conversations(statuses=None)
    known_tasks: dict[str, object] = {}
    run_owner: dict[str, str] = {}
    artifacts: set[str] = set()
    results: set[str] = set()
    for report in reports:
        try:
            task = await deployment.kernel.get_task(report.task_id)
        except ContractError as exc:
            raise RestoreValidationError(
                f"conversation validator cannot reconstruct task {report.task_id}"
            ) from exc
        known_tasks[report.task_id] = task
        artifacts.update(task.artifact_ids)
        results.update(task.result_ids)
        for run_id in task.run_ids:
            run_owner[run_id] = task.task_id
            run = await deployment.kernel.get_run(task.task_id, run_id)
            artifacts.update(run.artifact_ids)
            results.update(run.result_ids)

    file_ids = _file_ids(deployment)
    message_count = 0
    for conversation in conversations:
        _validate_scope(
            deployment,
            entity=f"conversation {conversation.id}",
            project_id=conversation.project_id,
            workspace_id=conversation.workspace_id,
        )
        for participant in conversation.participants:
            if participant.kind is ParticipantKind.AGENT:
                _require_agent(deployment, participant.id, f"conversation {conversation.id}")
            elif participant.kind is ParticipantKind.AGENT_TEAM:
                _require_team(deployment, participant.id, f"conversation {conversation.id}")
        if conversation.default_agent is not None:
            selection = conversation.default_agent
            if selection.kind is ParticipantKind.AGENT:
                if selection.revision is None:
                    _require_agent(deployment, selection.id, f"conversation {conversation.id}")
                else:
                    _require_agent_revision(
                        deployment,
                        selection.id,
                        selection.revision,
                        entity=f"conversation {conversation.id}",
                    )
            else:
                _require_team_revision(
                    deployment,
                    selection.id,
                    selection.revision,
                    entity=f"conversation {conversation.id}",
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
        for task_id in conversation.task_ids:
            if task_id not in known_tasks:
                raise RestoreValidationError(
                    f"conversation {conversation.id} references missing task {task_id}"
                )
        for run_id in conversation.run_ids:
            if run_id not in run_owner:
                raise RestoreValidationError(
                    f"conversation {conversation.id} references missing run {run_id}"
                )
        missing_artifacts = set(conversation.artifact_ids) - artifacts
        if missing_artifacts:
            raise RestoreValidationError(
                f"conversation {conversation.id} references missing artifacts "
                f"{sorted(missing_artifacts)!r}"
            )

        cursor: str | None = None
        while True:
            messages, cursor = await repository.list_messages(
                conversation.id,
                limit=200,
                cursor=cursor,
            )
            for message in messages:
                if message.model_config_id is not None:
                    _require_model(deployment, message.model_config_id, f"message {message.id}")
                for reference in message.references:
                    _validate_resource_reference(
                        deployment,
                        reference,
                        entity=f"message {message.id}",
                        known_tasks=known_tasks,
                        run_owner=run_owner,
                        file_ids=file_ids,
                        artifacts=artifacts,
                        results=results,
                    )
                for block in message.content:
                    if block.reference is not None:
                        _validate_resource_reference(
                            deployment,
                            block.reference,
                            entity=f"message {message.id}",
                            known_tasks=known_tasks,
                            run_owner=run_owner,
                            file_ids=file_ids,
                            artifacts=artifacts,
                            results=results,
                        )
                message_count += 1
            if cursor is None:
                break

    return len(conversations), message_count


def _validate_scope(
    deployment: SingleNodeDeployment,
    *,
    entity: str,
    project_id: str | None,
    workspace_id: str | None,
) -> None:
    if project_id is not None:
        try:
            deployment.scopes.get_project(project_id)
        except ContractError as exc:
            raise RestoreValidationError(
                f"{entity} references missing project {project_id}"
            ) from exc
    if workspace_id is not None:
        try:
            workspace = deployment.scopes.get_workspace(workspace_id)
        except ContractError as exc:
            raise RestoreValidationError(
                f"{entity} references missing workspace {workspace_id}"
            ) from exc
        if project_id is not None and workspace.project_id != project_id:
            raise RestoreValidationError(
                f"{entity} workspace {workspace_id} belongs to project {workspace.project_id}, "
                f"not {project_id}"
            )


def _require_agent(deployment: SingleNodeDeployment, agent_id: str, entity: str) -> None:
    try:
        deployment.agents.repository.get_agent(agent_id)
    except ContractError as exc:
        raise RestoreValidationError(f"{entity} references missing agent {agent_id}") from exc


def _require_agent_revision(
    deployment: SingleNodeDeployment,
    agent_id: str,
    revision: int,
    *,
    entity: str,
) -> None:
    try:
        deployment.agents.repository.get_agent_revision(agent_id, revision)
    except ContractError as exc:
        raise RestoreValidationError(
            f"{entity} references missing agent revision {agent_id}@{revision}"
        ) from exc


def _require_team(deployment: SingleNodeDeployment, team_id: str, entity: str) -> None:
    try:
        deployment.agents.repository.get_team(team_id)
    except ContractError as exc:
        raise RestoreValidationError(f"{entity} references missing agent team {team_id}") from exc


def _require_team_revision(
    deployment: SingleNodeDeployment,
    team_id: str,
    revision: int | None,
    *,
    entity: str,
) -> None:
    try:
        definition = deployment.agents.repository.get_team(team_id)
        deployment.agents.repository.get_team_revision(
            team_id,
            definition.current_revision if revision is None else revision,
        )
    except ContractError as exc:
        suffix = "current" if revision is None else str(revision)
        raise RestoreValidationError(
            f"{entity} references missing agent team revision {team_id}@{suffix}"
        ) from exc


def _require_model(deployment: SingleNodeDeployment, model_id: str, entity: str) -> None:
    try:
        deployment.models.get_model(model_id)
    except ContractError as exc:
        raise RestoreValidationError(
            f"{entity} references missing model configuration {model_id}"
        ) from exc


def _file_ids(deployment: SingleNodeDeployment) -> set[str]:
    database = deployment.config.database_dir / "files.sqlite3"
    try:
        with sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True) as connection:
            return {str(row[0]) for row in connection.execute("SELECT file_id FROM data_files")}
    except sqlite3.Error as exc:
        raise RestoreValidationError("cannot enumerate restored file references") from exc


def _validate_resource_reference(
    deployment: SingleNodeDeployment,
    reference: ResourceReference,
    *,
    entity: str,
    known_tasks: dict[str, object],
    run_owner: dict[str, str],
    file_ids: set[str],
    artifacts: set[str],
    results: set[str],
) -> None:
    if reference.kind is ReferenceKind.FILE and reference.id not in file_ids:
        raise RestoreValidationError(f"{entity} references missing file {reference.id}")
    if reference.kind is ReferenceKind.ARTIFACT and reference.id not in artifacts:
        raise RestoreValidationError(f"{entity} references missing artifact {reference.id}")
    if reference.kind is ReferenceKind.TASK and reference.id not in known_tasks:
        raise RestoreValidationError(f"{entity} references missing task {reference.id}")
    if reference.kind is ReferenceKind.RUN and reference.id not in run_owner:
        raise RestoreValidationError(f"{entity} references missing run {reference.id}")
    if reference.kind is ReferenceKind.RESULT and reference.id not in results:
        raise RestoreValidationError(f"{entity} references missing result {reference.id}")
    if reference.kind is ReferenceKind.AGENT:
        _require_agent(deployment, reference.id, entity)
    if reference.kind is ReferenceKind.AGENT_TEAM:
        _require_team(deployment, reference.id, entity)
    # Knowledge references are syntactically canonical but the current single-node composition
    # does not own a Knowledge service/store. Their backend-specific existence is therefore not
    # asserted here; when that service is composed it must register its own restore validator.
