"""Replaceable compensation persistence with deterministic in-memory and SQLite references."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from typing import Protocol, cast

from ai_multi_agent_platform.capabilities import (
    CompensationDescriptor,
    CompensationIdempotency,
    ReversibilityClassification,
    SideEffectClassification,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .models import (
    CompensationAutomation,
    CompensationFailureMode,
    CompensationGroup,
    CompensationPolicy,
    CompensationRequest,
    CompensationResult,
    CompensationStatus,
    CompensationTrigger,
    CompletedSideEffect,
)


class CompensationRepository(Protocol):
    def create_group(self, group: CompensationGroup) -> CompensationGroup: ...

    def get_group(self, group_id: str) -> CompensationGroup: ...

    def list_groups_for_plan(self, plan_id: str) -> tuple[CompensationGroup, ...]: ...

    def add_action(self, action: CompletedSideEffect) -> CompletedSideEffect: ...

    def get_action(self, action_id: str) -> CompletedSideEffect: ...

    def list_actions(self, group_id: str) -> tuple[CompletedSideEffect, ...]: ...

    def create_request(self, request: CompensationRequest) -> CompensationRequest: ...

    def find_request_by_key(self, idempotency_key: str) -> CompensationRequest | None: ...

    def get_request(self, compensation_id: str) -> CompensationRequest: ...

    def list_requests(self, group_id: str) -> tuple[CompensationRequest, ...]: ...

    def save_result(self, result: CompensationResult) -> CompensationResult: ...

    def get_result(self, compensation_id: str) -> CompensationResult | None: ...


class InMemoryCompensationRepository:
    def __init__(self) -> None:
        self._groups: dict[str, CompensationGroup] = {}
        self._actions: dict[str, CompletedSideEffect] = {}
        self._requests: dict[str, CompensationRequest] = {}
        self._request_keys: dict[str, str] = {}
        self._results: dict[str, CompensationResult] = {}

    def create_group(self, group: CompensationGroup) -> CompensationGroup:
        existing = self._groups.get(group.group_id)
        if existing is not None:
            if existing == group:
                return existing
            raise ContractError(ErrorCode.CONFLICT, "compensation group already exists")
        self._groups[group.group_id] = group
        return group

    def get_group(self, group_id: str) -> CompensationGroup:
        try:
            return self._groups[group_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, "compensation group not found") from exc

    def list_groups_for_plan(self, plan_id: str) -> tuple[CompensationGroup, ...]:
        return tuple(
            sorted(
                (group for group in self._groups.values() if group.plan_id == plan_id),
                key=lambda group: group.group_id,
            )
        )

    def add_action(self, action: CompletedSideEffect) -> CompletedSideEffect:
        self.get_group(action.group_id)
        existing = self._actions.get(action.action_id)
        if existing is not None:
            if existing == action:
                return existing
            raise ContractError(ErrorCode.CONFLICT, "compensation action already exists")
        self._actions[action.action_id] = action
        return action

    def get_action(self, action_id: str) -> CompletedSideEffect:
        try:
            return self._actions[action_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, "compensation action not found") from exc

    def list_actions(self, group_id: str) -> tuple[CompletedSideEffect, ...]:
        self.get_group(group_id)
        return tuple(
            sorted(
                (action for action in self._actions.values() if action.group_id == group_id),
                key=lambda action: (action.execution_order, action.action_id),
            )
        )

    def create_request(self, request: CompensationRequest) -> CompensationRequest:
        existing_id = self._request_keys.get(request.idempotency_key)
        if existing_id is not None:
            return self._requests[existing_id]
        existing = self._requests.get(request.compensation_id)
        if existing is not None:
            if existing == request:
                return existing
            raise ContractError(ErrorCode.CONFLICT, "compensation request already exists")
        self._requests[request.compensation_id] = request
        self._request_keys[request.idempotency_key] = request.compensation_id
        return request

    def find_request_by_key(self, idempotency_key: str) -> CompensationRequest | None:
        compensation_id = self._request_keys.get(idempotency_key)
        return None if compensation_id is None else self._requests[compensation_id]

    def get_request(self, compensation_id: str) -> CompensationRequest:
        try:
            return self._requests[compensation_id]
        except KeyError as exc:
            raise ContractError(ErrorCode.NOT_FOUND, "compensation request not found") from exc

    def list_requests(self, group_id: str) -> tuple[CompensationRequest, ...]:
        return tuple(
            sorted(
                (request for request in self._requests.values() if request.group_id == group_id),
                key=lambda request: (request.requested_at, request.compensation_id),
            )
        )

    def save_result(self, result: CompensationResult) -> CompensationResult:
        self.get_request(result.compensation_id)
        self._results[result.compensation_id] = result
        return result

    def get_result(self, compensation_id: str) -> CompensationResult | None:
        return self._results.get(compensation_id)


class SQLiteCompensationRepository:
    """Durable single-node store; original Task/Run/ToolInvocation history stays elsewhere."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS compensation_groups (
                    group_id TEXT PRIMARY KEY,
                    plan_id TEXT NOT NULL,
                    payload TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_compensation_groups_plan
                    ON compensation_groups(plan_id);

                CREATE TABLE IF NOT EXISTS compensation_actions (
                    action_id TEXT PRIMARY KEY,
                    group_id TEXT NOT NULL,
                    execution_order INTEGER NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY(group_id) REFERENCES compensation_groups(group_id)
                );
                CREATE INDEX IF NOT EXISTS idx_compensation_actions_group
                    ON compensation_actions(group_id, execution_order);

                CREATE TABLE IF NOT EXISTS compensation_requests (
                    compensation_id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    group_id TEXT NOT NULL,
                    requested_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY(group_id) REFERENCES compensation_groups(group_id)
                );
                CREATE INDEX IF NOT EXISTS idx_compensation_requests_group
                    ON compensation_requests(group_id, requested_at);

                CREATE TABLE IF NOT EXISTS compensation_results (
                    compensation_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    FOREIGN KEY(compensation_id)
                        REFERENCES compensation_requests(compensation_id)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def create_group(self, group: CompensationGroup) -> CompensationGroup:
        payload = _dump(_group_to_json(group))
        try:
            with self._connect() as connection:
                connection.execute(
                    "INSERT INTO compensation_groups(group_id, plan_id, payload) VALUES (?, ?, ?)",
                    (group.group_id, group.plan_id, payload),
                )
        except sqlite3.IntegrityError:
            existing = self.get_group(group.group_id)
            if existing == group:
                return existing
            raise ContractError(ErrorCode.CONFLICT, "compensation group already exists") from None
        return group

    def get_group(self, group_id: str) -> CompensationGroup:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM compensation_groups WHERE group_id = ?", (group_id,)
            ).fetchone()
        if row is None:
            raise ContractError(ErrorCode.NOT_FOUND, "compensation group not found")
        return _group_from_json(_load(row[0]))

    def list_groups_for_plan(self, plan_id: str) -> tuple[CompensationGroup, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT payload FROM compensation_groups WHERE plan_id = ? ORDER BY group_id",
                (plan_id,),
            ).fetchall()
        return tuple(_group_from_json(_load(row[0])) for row in rows)

    def add_action(self, action: CompletedSideEffect) -> CompletedSideEffect:
        self.get_group(action.group_id)
        payload = _dump(_action_to_json(action))
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO compensation_actions(action_id, group_id, execution_order, payload)
                    VALUES (?, ?, ?, ?)
                    """,
                    (action.action_id, action.group_id, action.execution_order, payload),
                )
        except sqlite3.IntegrityError:
            existing = self.get_action(action.action_id)
            if existing == action:
                return existing
            raise ContractError(ErrorCode.CONFLICT, "compensation action already exists") from None
        return action

    def get_action(self, action_id: str) -> CompletedSideEffect:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM compensation_actions WHERE action_id = ?", (action_id,)
            ).fetchone()
        if row is None:
            raise ContractError(ErrorCode.NOT_FOUND, "compensation action not found")
        return _action_from_json(_load(row[0]))

    def list_actions(self, group_id: str) -> tuple[CompletedSideEffect, ...]:
        self.get_group(group_id)
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload FROM compensation_actions
                WHERE group_id = ?
                ORDER BY execution_order, action_id
                """,
                (group_id,),
            ).fetchall()
        return tuple(_action_from_json(_load(row[0])) for row in rows)

    def create_request(self, request: CompensationRequest) -> CompensationRequest:
        payload = _dump(_request_to_json(request))
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO compensation_requests(
                        compensation_id, idempotency_key, group_id, requested_at, payload
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        request.compensation_id,
                        request.idempotency_key,
                        request.group_id,
                        request.requested_at.isoformat(),
                        payload,
                    ),
                )
        except sqlite3.IntegrityError:
            existing = self.find_request_by_key(request.idempotency_key)
            if existing is not None:
                return existing
            existing_by_id = self.get_request(request.compensation_id)
            if existing_by_id == request:
                return existing_by_id
            raise ContractError(ErrorCode.CONFLICT, "compensation request already exists") from None
        return request

    def find_request_by_key(self, idempotency_key: str) -> CompensationRequest | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM compensation_requests WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return None if row is None else _request_from_json(_load(row[0]))

    def get_request(self, compensation_id: str) -> CompensationRequest:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM compensation_requests WHERE compensation_id = ?",
                (compensation_id,),
            ).fetchone()
        if row is None:
            raise ContractError(ErrorCode.NOT_FOUND, "compensation request not found")
        return _request_from_json(_load(row[0]))

    def list_requests(self, group_id: str) -> tuple[CompensationRequest, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT payload FROM compensation_requests
                WHERE group_id = ?
                ORDER BY requested_at, compensation_id
                """,
                (group_id,),
            ).fetchall()
        return tuple(_request_from_json(_load(row[0])) for row in rows)

    def save_result(self, result: CompensationResult) -> CompensationResult:
        self.get_request(result.compensation_id)
        payload = _dump(_result_to_json(result))
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO compensation_results(compensation_id, payload)
                VALUES (?, ?)
                ON CONFLICT(compensation_id) DO UPDATE SET payload = excluded.payload
                """,
                (result.compensation_id, payload),
            )
        return result

    def get_result(self, compensation_id: str) -> CompensationResult | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM compensation_results WHERE compensation_id = ?",
                (compensation_id,),
            ).fetchone()
        return None if row is None else _result_from_json(_load(row[0]))


def _dump(value: dict[str, JsonValue]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _load(value: str) -> dict[str, JsonValue]:
    loaded = json.loads(value)
    if not isinstance(loaded, dict):
        raise ValueError("compensation persistence payload must be an object")
    return cast(dict[str, JsonValue], loaded)


def _descriptor_to_json(value: CompensationDescriptor | None) -> JsonValue:
    if value is None:
        return None
    return {
        "capability_id": value.capability_id,
        "version": value.version,
        "required_original_argument_keys": list(value.required_original_argument_keys),
        "requires_original_result_ref": value.requires_original_result_ref,
        "window_seconds": value.window_seconds,
        "side_effects": value.side_effects.value,
        "requires_approval": value.requires_approval,
        "idempotency": value.idempotency.value,
        "known_limitations": list(value.known_limitations),
    }


def _descriptor_from_json(value: JsonValue) -> CompensationDescriptor | None:
    if value is None:
        return None
    data = cast(dict[str, JsonValue], value)
    return CompensationDescriptor(
        capability_id=cast(str, data["capability_id"]),
        version=cast(str | None, data.get("version")),
        required_original_argument_keys=tuple(
            cast(list[str], data.get("required_original_argument_keys", []))
        ),
        requires_original_result_ref=bool(data.get("requires_original_result_ref", False)),
        window_seconds=cast(float | None, data.get("window_seconds")),
        side_effects=SideEffectClassification(cast(str, data["side_effects"])),
        requires_approval=bool(data.get("requires_approval", False)),
        idempotency=CompensationIdempotency(cast(str, data["idempotency"])),
        known_limitations=tuple(cast(list[str], data.get("known_limitations", []))),
    )


def _policy_to_json(policy: CompensationPolicy) -> dict[str, JsonValue]:
    return {
        "automation": policy.automation.value,
        "failure_mode": policy.failure_mode.value,
        "require_human_approval": policy.require_human_approval,
        "allow_newer_plan_revision": policy.allow_newer_plan_revision,
    }


def _policy_from_json(value: JsonValue) -> CompensationPolicy:
    data = cast(dict[str, JsonValue], value)
    return CompensationPolicy(
        automation=CompensationAutomation(cast(str, data["automation"])),
        failure_mode=CompensationFailureMode(cast(str, data["failure_mode"])),
        require_human_approval=bool(data["require_human_approval"]),
        allow_newer_plan_revision=bool(data["allow_newer_plan_revision"]),
    )


def _group_to_json(group: CompensationGroup) -> dict[str, JsonValue]:
    return {
        "group_id": group.group_id,
        "task_id": group.task_id,
        "plan_id": group.plan_id,
        "plan_revision": group.plan_revision,
        "project_id": group.project_id,
        "policy": _policy_to_json(group.policy),
        "created_at": group.created_at.isoformat(),
        "provenance_source": group.provenance_source,
    }


def _group_from_json(data: dict[str, JsonValue]) -> CompensationGroup:
    return CompensationGroup(
        group_id=cast(str, data["group_id"]),
        task_id=cast(str, data["task_id"]),
        plan_id=cast(str, data["plan_id"]),
        plan_revision=int(cast(int, data["plan_revision"])),
        project_id=cast(str | None, data.get("project_id")),
        policy=_policy_from_json(data["policy"]),
        created_at=datetime.fromisoformat(cast(str, data["created_at"])),
        provenance_source=cast(str, data["provenance_source"]),
    )


def _action_to_json(action: CompletedSideEffect) -> dict[str, JsonValue]:
    return {
        "action_id": action.action_id,
        "group_id": action.group_id,
        "task_id": action.task_id,
        "plan_id": action.plan_id,
        "plan_revision": action.plan_revision,
        "project_id": action.project_id,
        "step_id": action.step_id,
        "run_id": action.run_id,
        "tool_invocation_id": action.tool_invocation_id,
        "agent_id": action.agent_id,
        "capability_id": action.capability_id,
        "capability_version": action.capability_version,
        "reversibility": action.reversibility.value,
        "compensation": _descriptor_to_json(action.compensation),
        "original_arguments": dict(action.original_arguments),
        "compensation_arguments": dict(action.compensation_arguments),
        "execution_order": action.execution_order,
        "depends_on_action_ids": list(action.depends_on_action_ids),
        "original_result_ref": action.original_result_ref,
        "external_resource_ref": action.external_resource_ref,
        "artifact_refs": list(action.artifact_refs),
        "evidence_refs": list(action.evidence_refs),
        "completed_at": action.completed_at.isoformat(),
    }


def _action_from_json(data: dict[str, JsonValue]) -> CompletedSideEffect:
    return CompletedSideEffect(
        action_id=cast(str, data["action_id"]),
        group_id=cast(str, data["group_id"]),
        task_id=cast(str, data["task_id"]),
        plan_id=cast(str, data["plan_id"]),
        plan_revision=int(cast(int, data["plan_revision"])),
        project_id=cast(str | None, data.get("project_id")),
        step_id=cast(str, data["step_id"]),
        run_id=cast(str, data["run_id"]),
        tool_invocation_id=cast(str, data["tool_invocation_id"]),
        agent_id=cast(str, data["agent_id"]),
        capability_id=cast(str, data["capability_id"]),
        capability_version=cast(str, data["capability_version"]),
        reversibility=ReversibilityClassification(cast(str, data["reversibility"])),
        compensation=_descriptor_from_json(data["compensation"]),
        original_arguments=cast(dict[str, JsonValue], data["original_arguments"]),
        compensation_arguments=cast(dict[str, JsonValue], data["compensation_arguments"]),
        execution_order=int(cast(int, data["execution_order"])),
        depends_on_action_ids=tuple(cast(list[str], data["depends_on_action_ids"])),
        original_result_ref=cast(str | None, data.get("original_result_ref")),
        external_resource_ref=cast(str | None, data.get("external_resource_ref")),
        artifact_refs=tuple(cast(list[str], data.get("artifact_refs", []))),
        evidence_refs=tuple(cast(list[str], data.get("evidence_refs", []))),
        completed_at=datetime.fromisoformat(cast(str, data["completed_at"])),
    )


def _request_to_json(request: CompensationRequest) -> dict[str, JsonValue]:
    return {
        "compensation_id": request.compensation_id,
        "idempotency_key": request.idempotency_key,
        "group_id": request.group_id,
        "action_id": request.action_id,
        "original_task_id": request.original_task_id,
        "original_plan_id": request.original_plan_id,
        "original_plan_revision": request.original_plan_revision,
        "original_project_id": request.original_project_id,
        "original_step_id": request.original_step_id,
        "original_run_id": request.original_run_id,
        "original_tool_invocation_id": request.original_tool_invocation_id,
        "original_result_ref": request.original_result_ref,
        "external_resource_ref": request.external_resource_ref,
        "requested_capability_id": request.requested_capability_id,
        "requested_capability_version": request.requested_capability_version,
        "trigger": request.trigger.value,
        "reason": request.reason,
        "actor_ref": request.actor_ref,
        "correlation_id": request.correlation_id,
        "approval_id": request.approval_id,
        "requested_at": request.requested_at.isoformat(),
        "provenance_source": request.provenance_source,
    }


def _request_from_json(data: dict[str, JsonValue]) -> CompensationRequest:
    return CompensationRequest(
        compensation_id=cast(str, data["compensation_id"]),
        idempotency_key=cast(str, data["idempotency_key"]),
        group_id=cast(str, data["group_id"]),
        action_id=cast(str, data["action_id"]),
        original_task_id=cast(str, data["original_task_id"]),
        original_plan_id=cast(str, data["original_plan_id"]),
        original_plan_revision=int(cast(int, data["original_plan_revision"])),
        original_project_id=cast(str | None, data.get("original_project_id")),
        original_step_id=cast(str, data["original_step_id"]),
        original_run_id=cast(str, data["original_run_id"]),
        original_tool_invocation_id=cast(str, data["original_tool_invocation_id"]),
        original_result_ref=cast(str | None, data.get("original_result_ref")),
        external_resource_ref=cast(str | None, data.get("external_resource_ref")),
        requested_capability_id=cast(str | None, data.get("requested_capability_id")),
        requested_capability_version=cast(str | None, data.get("requested_capability_version")),
        trigger=CompensationTrigger(cast(str, data["trigger"])),
        reason=cast(str, data["reason"]),
        actor_ref=cast(str, data["actor_ref"]),
        correlation_id=cast(str, data["correlation_id"]),
        approval_id=cast(str | None, data.get("approval_id")),
        requested_at=datetime.fromisoformat(cast(str, data["requested_at"])),
        provenance_source=cast(str, data["provenance_source"]),
    )


def _result_to_json(result: CompensationResult) -> dict[str, JsonValue]:
    return {
        "compensation_id": result.compensation_id,
        "status": result.status.value,
        "execution_task_id": result.execution_task_id,
        "execution_run_id": result.execution_run_id,
        "execution_agent_id": result.execution_agent_id,
        "invocation_id": result.invocation_id,
        "canonical_tool_invocation_id": result.canonical_tool_invocation_id,
        "provider_id": result.provider_id,
        "approval_id": result.approval_id,
        "result_ref": result.result_ref,
        "artifact_refs": list(result.artifact_refs),
        "evidence_refs": list(result.evidence_refs),
        "error_code": result.error_code,
        "error_message": result.error_message,
        "manual_intervention_required": result.manual_intervention_required,
        "started_at": None if result.started_at is None else result.started_at.isoformat(),
        "completed_at": None if result.completed_at is None else result.completed_at.isoformat(),
        "verification_ref": result.verification_ref,
    }


def _result_from_json(data: dict[str, JsonValue]) -> CompensationResult:
    started = cast(str | None, data.get("started_at"))
    completed = cast(str | None, data.get("completed_at"))
    return CompensationResult(
        compensation_id=cast(str, data["compensation_id"]),
        status=CompensationStatus(cast(str, data["status"])),
        execution_task_id=cast(str | None, data.get("execution_task_id")),
        execution_run_id=cast(str | None, data.get("execution_run_id")),
        execution_agent_id=cast(str | None, data.get("execution_agent_id")),
        invocation_id=cast(str | None, data.get("invocation_id")),
        canonical_tool_invocation_id=cast(str | None, data.get("canonical_tool_invocation_id")),
        provider_id=cast(str | None, data.get("provider_id")),
        approval_id=cast(str | None, data.get("approval_id")),
        result_ref=cast(str | None, data.get("result_ref")),
        artifact_refs=tuple(cast(list[str], data.get("artifact_refs", []))),
        evidence_refs=tuple(cast(list[str], data.get("evidence_refs", []))),
        error_code=cast(str | None, data.get("error_code")),
        error_message=cast(str | None, data.get("error_message")),
        manual_intervention_required=bool(data.get("manual_intervention_required", False)),
        started_at=None if started is None else datetime.fromisoformat(started),
        completed_at=None if completed is None else datetime.fromisoformat(completed),
        verification_ref=cast(str | None, data.get("verification_ref")),
    )


def _validate_action_dependencies(actions: Iterable[CompletedSideEffect]) -> None:
    action_tuple = tuple(actions)
    ids = {action.action_id for action in action_tuple}
    for action in action_tuple:
        missing = set(action.depends_on_action_ids) - ids
        if missing:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "compensation action dependency references an action outside the explicit group",
            )
