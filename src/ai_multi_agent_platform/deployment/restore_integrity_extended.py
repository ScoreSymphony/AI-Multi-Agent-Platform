"""Cross-store restore-integrity checks for durable single-node subsystems."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ai_multi_agent_platform.backup.integrity import RestoreValidationError
from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.kernel import RecoveryReport
from ai_multi_agent_platform.security import ActorType

from .single_node import SingleNodeDeployment

DeploymentRestoreValidator = Callable[[tuple[RecoveryReport, ...]], Awaitable[tuple[str, ...]]]


@dataclass(frozen=True, slots=True)
class _CanonicalIndex:
    task_ids: frozenset[str]
    task_projects: dict[str, str | None]
    run_owner: dict[str, str]
    project_ids: frozenset[str]
    scope_workspace_projects: dict[str, str]
    ready_files: dict[str, str]
    artifact_ids: frozenset[str]
    result_ids: frozenset[str]
    user_ids: frozenset[str]
    automation_ids: frozenset[str]


def single_node_extended_restore_integrity_validators(
    deployment: SingleNodeDeployment,
) -> tuple[DeploymentRestoreValidator, ...]:
    """Return strict cross-store validators not owned by backup core.

    The validator intentionally returns no additional report token. This keeps the version-one
    restore-report check vocabulary stable while making readiness depend on these stronger checks.
    """

    async def cross_store(reports: tuple[RecoveryReport, ...]) -> tuple[str, ...]:
        index = await _build_index(deployment, reports)
        workspace_projects = _validate_workspace_provider(deployment, index)
        automations = await _validate_automations(deployment, index, workspace_projects)
        expanded = _CanonicalIndex(
            task_ids=index.task_ids,
            task_projects=index.task_projects,
            run_owner=index.run_owner,
            project_ids=index.project_ids,
            scope_workspace_projects=index.scope_workspace_projects,
            ready_files=index.ready_files,
            artifact_ids=index.artifact_ids,
            result_ids=index.result_ids,
            user_ids=index.user_ids,
            automation_ids=frozenset(automations),
        )
        _validate_scope_owners(deployment, expanded)
        _validate_authorization(deployment, expanded, workspace_projects)
        _validate_authentication(deployment, expanded)
        _validate_verification(deployment, expanded)
        return ()

    return (cross_store,)


async def _build_index(
    deployment: SingleNodeDeployment,
    reports: tuple[RecoveryReport, ...],
) -> _CanonicalIndex:
    task_ids: set[str] = set()
    task_projects: dict[str, str | None] = {}
    run_owner: dict[str, str] = {}
    artifact_ids: set[str] = set()
    result_ids: set[str] = set()
    for report in reports:
        task = await deployment.kernel.get_task(report.task_id)
        task_ids.add(task.task_id)
        task_projects[task.task_id] = task.task.project_id
        artifact_ids.update(task.artifact_ids)
        result_ids.update(task.result_ids)
        for run_id in task.run_ids:
            run = await deployment.kernel.get_run(task.task_id, run_id)
            existing = run_owner.get(run_id)
            if existing is not None and existing != task.task_id:
                raise RestoreValidationError(
                    f"canonical run {run_id} is referenced by multiple tasks"
                )
            run_owner[run_id] = task.task_id
            artifact_ids.update(run.artifact_ids)
            result_ids.update(run.result_ids)

    project_ids = frozenset(project.id for project in deployment.scopes.list_projects())
    scope_workspace_projects = {
        workspace.id: workspace.project_id for workspace in deployment.scopes.list_workspaces()
    }
    return _CanonicalIndex(
        task_ids=frozenset(task_ids),
        task_projects=task_projects,
        run_owner=run_owner,
        project_ids=project_ids,
        scope_workspace_projects=scope_workspace_projects,
        ready_files=_ready_file_hashes(deployment),
        artifact_ids=frozenset(artifact_ids),
        result_ids=frozenset(result_ids),
        user_ids=frozenset(deployment.authentication.store.users),
        automation_ids=frozenset(),
    )


def _ready_file_hashes(deployment: SingleNodeDeployment) -> dict[str, str]:
    database = deployment.config.database_dir / "files.sqlite3"
    try:
        with sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True) as connection:
            rows = connection.execute(
                "SELECT file_id, sha256, state FROM data_files ORDER BY file_id"
            ).fetchall()
    except sqlite3.Error as exc:
        raise RestoreValidationError("cannot enumerate restored file metadata") from exc
    return {str(row[0]): str(row[1]) for row in rows if str(row[2]) == "ready"}


def _validate_workspace_provider(
    deployment: SingleNodeDeployment,
    index: _CanonicalIndex,
) -> dict[str, str]:
    database = deployment.config.database_dir / "workspaces.sqlite3"
    try:
        with sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True) as connection:
            connection.row_factory = sqlite3.Row
            workspace_rows = connection.execute(
                "SELECT workspace_id, project_id, owner_type, owner_id, base_snapshot_id, "
                "source_refs_json FROM workspace_metadata ORDER BY workspace_id"
            ).fetchall()
            snapshot_rows = connection.execute(
                "SELECT snapshot_id, workspace_id, revision, files_json, content_checksum, "
                "source_refs_json, parent_snapshot_id, artifact_ids_json "
                "FROM workspace_snapshots ORDER BY snapshot_id"
            ).fetchall()
            head_rows = connection.execute(
                "SELECT workspace_id, snapshot_id FROM workspace_heads ORDER BY workspace_id"
            ).fetchall()
    except sqlite3.Error as exc:
        raise RestoreValidationError("cannot inspect restored workspace metadata") from exc

    workspaces = {str(row["workspace_id"]): row for row in workspace_rows}
    snapshots = {str(row["snapshot_id"]): row for row in snapshot_rows}
    heads = {str(row["workspace_id"]): str(row["snapshot_id"]) for row in head_rows}
    workspace_projects: dict[str, str] = dict(index.scope_workspace_projects)

    for workspace_id, row in workspaces.items():
        project_id = str(row["project_id"])
        if project_id not in index.project_ids:
            raise RestoreValidationError(
                f"workspace provider {workspace_id} references missing project {project_id}"
            )
        existing_project = workspace_projects.get(workspace_id)
        if existing_project is not None and existing_project != project_id:
            raise RestoreValidationError(
                f"workspace {workspace_id} has conflicting project identities: "
                f"{existing_project} vs {project_id}"
            )
        workspace_projects[workspace_id] = project_id
        if str(row["owner_type"]) == "user" and str(row["owner_id"]) not in index.user_ids:
            raise RestoreValidationError(
                f"workspace provider {workspace_id} references missing user {row['owner_id']}"
            )
        base_snapshot_id = str(row["base_snapshot_id"])
        head_id = heads.get(workspace_id)
        if head_id is None or head_id != base_snapshot_id:
            raise RestoreValidationError(
                f"workspace provider {workspace_id} has invalid canonical head snapshot"
            )
        base = snapshots.get(base_snapshot_id)
        if base is None or str(base["workspace_id"]) != workspace_id:
            raise RestoreValidationError(
                f"workspace provider {workspace_id} references missing base snapshot {base_snapshot_id}"
            )
        _validate_workspace_sources(
            _json_array(row["source_refs_json"], f"workspace {workspace_id} source_refs"),
            entity=f"workspace {workspace_id}",
            snapshots=snapshots,
            artifact_ids=index.artifact_ids,
        )

    for workspace_id, snapshot_id in heads.items():
        if workspace_id not in workspaces:
            raise RestoreValidationError(
                f"workspace head references missing workspace {workspace_id}"
            )
        snapshot = snapshots.get(snapshot_id)
        if snapshot is None or str(snapshot["workspace_id"]) != workspace_id:
            raise RestoreValidationError(
                f"workspace {workspace_id} head references invalid snapshot {snapshot_id}"
            )

    for snapshot_id, row in snapshots.items():
        workspace_id = str(row["workspace_id"])
        if workspace_id not in workspaces:
            raise RestoreValidationError(
                f"workspace snapshot {snapshot_id} references missing workspace {workspace_id}"
            )
        parent_id = None if row["parent_snapshot_id"] is None else str(row["parent_snapshot_id"])
        if parent_id is not None:
            parent = snapshots.get(parent_id)
            if parent is None or str(parent["workspace_id"]) != workspace_id:
                raise RestoreValidationError(
                    f"workspace snapshot {snapshot_id} references invalid parent {parent_id}"
                )

        files = _json_array(row["files_json"], f"workspace snapshot {snapshot_id} files")
        normalized_files: list[tuple[str, str, str]] = []
        seen_paths: set[str] = set()
        for raw_file in files:
            file_data = _json_object(raw_file, f"workspace snapshot {snapshot_id} file")
            relative_path = _string(file_data.get("relative_path"), "workspace relative_path")
            file_id = _string(file_data.get("file_id"), "workspace file_id")
            sha256 = _string(file_data.get("sha256"), "workspace file sha256")
            if relative_path in seen_paths:
                raise RestoreValidationError(
                    f"workspace snapshot {snapshot_id} contains duplicate path {relative_path!r}"
                )
            seen_paths.add(relative_path)
            canonical_hash = index.ready_files.get(file_id)
            if canonical_hash is None:
                raise RestoreValidationError(
                    f"workspace snapshot {snapshot_id} references missing ready file {file_id}"
                )
            if canonical_hash != sha256:
                raise RestoreValidationError(
                    f"workspace snapshot {snapshot_id} file checksum differs for {file_id}"
                )
            normalized_files.append((relative_path, file_id, sha256))
        if _workspace_snapshot_checksum(normalized_files) != str(row["content_checksum"]):
            raise RestoreValidationError(
                f"workspace snapshot {snapshot_id} content checksum is inconsistent"
            )

        artifact_ids = _string_array(
            row["artifact_ids_json"], f"workspace snapshot {snapshot_id} artifact_ids"
        )
        missing_artifacts = set(artifact_ids) - set(index.artifact_ids)
        if missing_artifacts:
            raise RestoreValidationError(
                f"workspace snapshot {snapshot_id} references missing artifacts "
                f"{sorted(missing_artifacts)!r}"
            )
        _validate_workspace_sources(
            _json_array(row["source_refs_json"], f"workspace snapshot {snapshot_id} source_refs"),
            entity=f"workspace snapshot {snapshot_id}",
            snapshots=snapshots,
            artifact_ids=index.artifact_ids,
        )

    return workspace_projects


def _validate_workspace_sources(
    raw_sources: list[Any],
    *,
    entity: str,
    snapshots: dict[str, sqlite3.Row],
    artifact_ids: frozenset[str],
) -> None:
    for raw_source in raw_sources:
        source = _json_object(raw_source, f"{entity} source")
        kind = _string(source.get("kind"), "workspace source kind")
        ref = _string(source.get("ref"), "workspace source ref")
        if kind == "snapshot":
            snapshot = snapshots.get(ref)
            if snapshot is None:
                raise RestoreValidationError(f"{entity} references missing source snapshot {ref}")
            revision = source.get("revision")
            if revision is not None and str(revision) != str(snapshot["revision"]):
                raise RestoreValidationError(
                    f"{entity} source snapshot {ref} revision is inconsistent"
                )
            checksum = source.get("checksum")
            if checksum is not None and str(checksum) != str(snapshot["content_checksum"]):
                raise RestoreValidationError(
                    f"{entity} source snapshot {ref} checksum is inconsistent"
                )
        elif kind == "artifact" and ref not in artifact_ids:
            raise RestoreValidationError(f"{entity} references missing source artifact {ref}")
        elif kind not in {"empty", "files", "artifact", "repository", "template"}:
            raise RestoreValidationError(f"{entity} has unsupported workspace source kind {kind!r}")


async def _validate_automations(
    deployment: SingleNodeDeployment,
    index: _CanonicalIndex,
    workspace_projects: dict[str, str],
) -> set[str]:
    try:
        automations = await deployment.control_plane.automation_service.list_automations()
        deliveries = await deployment.control_plane.automation_service.list_deliveries()
    except (ContractError, ValueError) as exc:
        raise RestoreValidationError("cannot reconstruct restored automation state") from exc

    automation_ids = {automation.id for automation in automations}
    expanded_ids = frozenset(automation_ids)
    for automation in automations:
        _validate_owner(
            automation.identity.owner_type,
            automation.identity.owner_id,
            index=index,
            automation_ids=expanded_ids,
            entity=f"automation {automation.id}",
        )
        _validate_principal(
            deployment,
            automation.identity.principal_ref,
            user_ids=index.user_ids,
            automation_ids=expanded_ids,
            entity=f"automation {automation.id}",
        )
        _validate_project_workspace_pair(
            project_id=automation.project_id,
            workspace_id=automation.workspace_id,
            index=index,
            workspace_projects=workspace_projects,
            entity=f"automation {automation.id}",
        )
        _validate_project_workspace_pair(
            project_id=automation.task_template.project_id,
            workspace_id=automation.task_template.workspace_id,
            index=index,
            workspace_projects=workspace_projects,
            entity=f"automation {automation.id} task template",
        )

    for delivery in deliveries:
        if delivery.automation_id not in automation_ids:
            raise RestoreValidationError(
                f"automation delivery {delivery.id} references missing automation "
                f"{delivery.automation_id}"
            )
        if delivery.generated_task_id is not None and delivery.generated_task_id not in index.task_ids:
            raise RestoreValidationError(
                f"automation delivery {delivery.id} references missing generated task "
                f"{delivery.generated_task_id}"
            )
    return automation_ids


def _validate_authorization(
    deployment: SingleNodeDeployment,
    index: _CanonicalIndex,
    workspace_projects: dict[str, str],
) -> None:
    database = deployment.config.database_dir / "authorization.sqlite3"
    try:
        with sqlite3.connect(f"file:{database}?mode=ro&immutable=1", uri=True) as connection:
            rows = connection.execute(
                "SELECT principal_ref, project_ids_json, workspace_ids_json "
                "FROM authorization_policies ORDER BY principal_ref"
            ).fetchall()
    except sqlite3.Error as exc:
        raise RestoreValidationError("cannot inspect restored authorization policies") from exc

    for principal_ref, project_json, workspace_json in rows:
        principal = str(principal_ref)
        _validate_principal(
            deployment,
            principal,
            user_ids=index.user_ids,
            automation_ids=index.automation_ids,
            entity=f"authorization policy {principal}",
        )
        projects = _string_array(project_json, f"authorization policy {principal} projects")
        missing_projects = set(projects) - set(index.project_ids)
        if missing_projects:
            raise RestoreValidationError(
                f"authorization policy {principal} references missing projects "
                f"{sorted(missing_projects)!r}"
            )
        workspaces = _string_array(
            workspace_json, f"authorization policy {principal} workspaces"
        )
        missing_workspaces = set(workspaces) - set(workspace_projects)
        if missing_workspaces:
            raise RestoreValidationError(
                f"authorization policy {principal} references missing workspaces "
                f"{sorted(missing_workspaces)!r}"
            )


def _validate_authentication(
    deployment: SingleNodeDeployment,
    index: _CanonicalIndex,
) -> None:
    for credential in deployment.authentication.store.credentials.values():
        if credential.actor_type is ActorType.HUMAN:
            if credential.owner_id not in index.user_ids:
                raise RestoreValidationError(
                    f"credential {credential.credential_id} references missing user "
                    f"{credential.owner_id}"
                )
        elif credential.actor_type is ActorType.AUTOMATION:
            if credential.owner_id not in index.automation_ids:
                raise RestoreValidationError(
                    f"credential {credential.credential_id} references missing automation "
                    f"{credential.owner_id}"
                )
        elif credential.actor_type is ActorType.AGENT:
            _require_agent(deployment, credential.owner_id, f"credential {credential.credential_id}")
        # SERVICE/WORKER/INTEGRATION owners are intentionally not asserted here because the
        # current SingleNodeDeployment has no composed canonical registry for those identities.


def _validate_verification(
    deployment: SingleNodeDeployment,
    index: _CanonicalIndex,
) -> None:
    policies = tuple(deployment.verification._policies.values())
    for policy in policies:
        missing_tasks = set(policy.scope.task_ids) - set(index.task_ids)
        missing_projects = set(policy.scope.project_ids) - set(index.project_ids)
        if missing_tasks:
            raise RestoreValidationError(
                f"verification policy {policy.policy_id}@{policy.version} references missing tasks "
                f"{sorted(missing_tasks)!r}"
            )
        if missing_projects:
            raise RestoreValidationError(
                f"verification policy {policy.policy_id}@{policy.version} references missing projects "
                f"{sorted(missing_projects)!r}"
            )
        for agent_id in policy.scope.agent_ids:
            _require_agent(
                deployment,
                agent_id,
                f"verification policy {policy.policy_id}@{policy.version}",
            )
        if policy.creator_ref is not None:
            _validate_principal(
                deployment,
                policy.creator_ref,
                user_ids=index.user_ids,
                automation_ids=index.automation_ids,
                entity=f"verification policy {policy.policy_id}@{policy.version}",
            )

    for request in deployment.verification.snapshot_requests():
        if request.task_id not in index.task_ids:
            raise RestoreValidationError(
                f"verification request {request.verification_id} references missing task "
                f"{request.task_id}"
            )
        if request.run_id is not None and index.run_owner.get(request.run_id) != request.task_id:
            raise RestoreValidationError(
                f"verification request {request.verification_id} references invalid run "
                f"{request.run_id}"
            )
        if request.project_id is not None:
            if request.project_id not in index.project_ids:
                raise RestoreValidationError(
                    f"verification request {request.verification_id} references missing project "
                    f"{request.project_id}"
                )
            task_project = index.task_projects.get(request.task_id)
            if task_project is not None and task_project != request.project_id:
                raise RestoreValidationError(
                    f"verification request {request.verification_id} project differs from its task"
                )
        if request.result_id is not None and request.result_id not in index.result_ids:
            raise RestoreValidationError(
                f"verification request {request.verification_id} references missing result "
                f"{request.result_id}"
            )
        missing_artifacts = set(request.artifact_ids) - set(index.artifact_ids)
        if missing_artifacts:
            raise RestoreValidationError(
                f"verification request {request.verification_id} references missing artifacts "
                f"{sorted(missing_artifacts)!r}"
            )
        _validate_verification_subject(
            request.subject.subject_type,
            request.subject.subject_id,
            index=index,
            entity=f"verification request {request.verification_id}",
        )
        if request.producer is not None:
            _validate_agent_model_identity(
                deployment,
                agent_id=request.producer.agent_id,
                agent_revision=request.producer.agent_revision,
                model_config_id=request.producer.model_config_id,
                provider_id=request.producer.provider_id,
                entity=f"verification request {request.verification_id} producer",
            )

        result = deployment.verification.result_for(request.verification_id)
        if result is None:
            continue
        missing_evidence = set(result.evidence_artifact_ids) - set(index.artifact_ids)
        if missing_evidence:
            raise RestoreValidationError(
                f"verification result {result.verification_result_id} references missing evidence "
                f"artifacts {sorted(missing_evidence)!r}"
            )
        _validate_agent_model_identity(
            deployment,
            agent_id=result.verifier.agent_id,
            agent_revision=result.verifier.agent_revision,
            model_config_id=result.verifier.model_config_id,
            provider_id=result.verifier.provider_id,
            entity=f"verification result {result.verification_result_id} verifier",
        )

    completion = deployment.verification_runtime._completion
    for requirement in completion._requirements.values():
        if requirement.task_id not in index.task_ids:
            raise RestoreValidationError(
                f"verification requirement references missing task {requirement.task_id}"
            )
        if requirement.subject is not None:
            _validate_verification_subject(
                requirement.subject.subject_type,
                requirement.subject.subject_id,
                index=index,
                entity=f"verification requirement for task {requirement.task_id}",
            )


def _validate_scope_owners(deployment: SingleNodeDeployment, index: _CanonicalIndex) -> None:
    for project in deployment.scopes.list_projects():
        _validate_owner(
            project.owner_ref.type,
            project.owner_ref.id,
            index=index,
            automation_ids=index.automation_ids,
            entity=f"project {project.id}",
        )
    for workspace in deployment.scopes.list_workspaces():
        _validate_owner(
            workspace.owner_type,
            workspace.owner_id,
            index=index,
            automation_ids=index.automation_ids,
            entity=f"workspace identity {workspace.id}",
        )


def _validate_owner(
    owner_type: str,
    owner_id: str,
    *,
    index: _CanonicalIndex,
    automation_ids: frozenset[str],
    entity: str,
) -> None:
    if owner_type == "user" and owner_id not in index.user_ids:
        raise RestoreValidationError(f"{entity} references missing user owner {owner_id}")
    if owner_type == "service":
        return
    if owner_type == "team":
        try:
            index_value = owner_id
            # Agent Teams are the only durable team registry composed by SingleNode today.
            # If the identifier has the Agent Team canonical shape, require that definition.
            if index_value.startswith(("agent_team_", "agent_team:", "agent-team:")):
                raise LookupError
        except LookupError:
            return
    # Organization/team owner registries are not yet composed by the current single-node profile.
    # Their opaque IDs therefore cannot be existence-checked here without inventing authority.


def _validate_project_workspace_pair(
    *,
    project_id: str | None,
    workspace_id: str | None,
    index: _CanonicalIndex,
    workspace_projects: dict[str, str],
    entity: str,
) -> None:
    if project_id is not None and project_id not in index.project_ids:
        raise RestoreValidationError(f"{entity} references missing project {project_id}")
    if workspace_id is not None:
        workspace_project = workspace_projects.get(workspace_id)
        if workspace_project is None:
            raise RestoreValidationError(f"{entity} references missing workspace {workspace_id}")
        if project_id is not None and workspace_project != project_id:
            raise RestoreValidationError(
                f"{entity} workspace {workspace_id} belongs to project {workspace_project}, "
                f"not {project_id}"
            )


def _validate_principal(
    deployment: SingleNodeDeployment,
    principal_ref: str,
    *,
    user_ids: frozenset[str],
    automation_ids: frozenset[str],
    entity: str,
) -> None:
    if principal_ref.startswith(("user_", "user:")):
        if principal_ref not in user_ids:
            raise RestoreValidationError(f"{entity} references missing user principal {principal_ref}")
        return
    if principal_ref.startswith(("agent_team_", "agent_team:", "agent-team:")):
        try:
            deployment.agents.repository.get_team(principal_ref)
        except ContractError as exc:
            raise RestoreValidationError(
                f"{entity} references missing agent team principal {principal_ref}"
            ) from exc
        return
    if principal_ref.startswith(("agent_", "agent:")):
        _require_agent(deployment, principal_ref, entity)
        return
    if principal_ref.startswith(("automation_", "automation:")) and principal_ref not in automation_ids:
        raise RestoreValidationError(
            f"{entity} references missing automation principal {principal_ref}"
        )


def _require_agent(deployment: SingleNodeDeployment, agent_id: str, entity: str) -> None:
    try:
        deployment.agents.repository.get_agent(agent_id)
    except ContractError as exc:
        raise RestoreValidationError(f"{entity} references missing agent {agent_id}") from exc


def _validate_agent_model_identity(
    deployment: SingleNodeDeployment,
    *,
    agent_id: str | None,
    agent_revision: int | None,
    model_config_id: str | None,
    provider_id: str | None,
    entity: str,
) -> None:
    if agent_id is not None:
        if agent_revision is None:
            raise RestoreValidationError(f"{entity} has agent identity without revision")
        try:
            deployment.agents.repository.get_agent_revision(agent_id, agent_revision)
        except ContractError as exc:
            raise RestoreValidationError(
                f"{entity} references missing agent revision {agent_id}@{agent_revision}"
            ) from exc
    if model_config_id is not None:
        try:
            model = deployment.models.get_model(model_config_id)
        except ContractError as exc:
            raise RestoreValidationError(
                f"{entity} references missing model configuration {model_config_id}"
            ) from exc
        if provider_id is not None and model.provider_id != provider_id:
            raise RestoreValidationError(
                f"{entity} provider {provider_id} differs from model provider {model.provider_id}"
            )


def _validate_verification_subject(
    subject_type: str,
    subject_id: str,
    *,
    index: _CanonicalIndex,
    entity: str,
) -> None:
    if subject_type == "result" and subject_id not in index.result_ids:
        raise RestoreValidationError(f"{entity} references missing result subject {subject_id}")
    if subject_type == "artifact" and subject_id not in index.artifact_ids:
        raise RestoreValidationError(f"{entity} references missing artifact subject {subject_id}")


def _workspace_snapshot_checksum(files: list[tuple[str, str, str]]) -> str:
    digest = hashlib.sha256()
    for relative_path, file_id, sha256 in sorted(files, key=lambda item: item[0]):
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(file_id.encode("ascii"))
        digest.update(b"\0")
        digest.update(sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _json_array(value: object, field: str) -> list[Any]:
    try:
        parsed = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise RestoreValidationError(f"{field} is invalid JSON") from exc
    if not isinstance(parsed, list):
        raise RestoreValidationError(f"{field} must be a JSON array")
    return parsed


def _string_array(value: object, field: str) -> list[str]:
    parsed = _json_array(value, field)
    if any(not isinstance(item, str) for item in parsed):
        raise RestoreValidationError(f"{field} must contain only strings")
    return [str(item) for item in parsed]


def _json_object(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RestoreValidationError(f"{field} must be a JSON object")
    return {str(key): item for key, item in value.items()}


def _string(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RestoreValidationError(f"{field} must be a non-blank string")
    return value
