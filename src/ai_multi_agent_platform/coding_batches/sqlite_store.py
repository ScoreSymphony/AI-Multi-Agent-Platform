"""Restart-durable SQLite persistence for issue #872 coding-batch composition state."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, cast

from .models import (
    BatchAggregationPolicy,
    CheckState,
    CodingBatch,
    CodingWorkItem,
    CodingWorkstream,
    CombinedValidationEvidence,
    IntegrationCandidate,
    IntegrationConflict,
    IntegrationRepairAttempt,
    IntegrationState,
    OverlapDecision,
    OverlapKind,
    RepairAttemptState,
    RequiredCheck,
    VerificationEvidence,
    WorkstreamProvenance,
    WorkstreamResult,
    WorkstreamState,
)

_SCHEMA_VERSION = 1


class SqliteCodingBatchStore:
    """Durable local/self-hosted `CodingBatchStore` implementation.

    The store persists only #872 composition/provenance state. Canonical Task, Plan, Step,
    Workspace, Repository, AgentRun, Verification and Approval resources remain owned by their
    respective subsystems and are referenced by identity only.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS coding_batch_metadata (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    schema_version INTEGER NOT NULL
                )
                """
            )
            row = connection.execute(
                "SELECT schema_version FROM coding_batch_metadata WHERE singleton = 1"
            ).fetchone()
            if row is None:
                connection.execute(
                    "INSERT INTO coding_batch_metadata(singleton, schema_version) VALUES (1, ?)",
                    (_SCHEMA_VERSION,),
                )
            elif int(row["schema_version"]) != _SCHEMA_VERSION:
                raise ValueError("unsupported coding-batch SQLite schema version")
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS coding_batches (
                    batch_id TEXT PRIMARY KEY,
                    request_key TEXT NOT NULL UNIQUE,
                    payload_json TEXT NOT NULL
                )
                """
            )

    def get(self, batch_id: str) -> CodingBatch | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM coding_batches WHERE batch_id = ?",
                (batch_id,),
            ).fetchone()
        return None if row is None else _decode_batch(str(row["payload_json"]))

    def get_by_request_key(self, request_key: str) -> CodingBatch | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload_json FROM coding_batches WHERE request_key = ?",
                (request_key,),
            ).fetchone()
        return None if row is None else _decode_batch(str(row["payload_json"]))

    def save(self, batch: CodingBatch) -> None:
        encoded = _encode_batch(batch)
        try:
            with self._connect() as connection:
                existing = connection.execute(
                    "SELECT batch_id FROM coding_batches WHERE request_key = ?",
                    (batch.request_key,),
                ).fetchone()
                if existing is not None and str(existing["batch_id"]) != batch.batch_id:
                    raise ValueError("request_key already belongs to a different coding batch")
                connection.execute(
                    """
                    INSERT INTO coding_batches(batch_id, request_key, payload_json)
                    VALUES (?, ?, ?)
                    ON CONFLICT(batch_id) DO UPDATE SET
                        request_key = excluded.request_key,
                        payload_json = excluded.payload_json
                    """,
                    (batch.batch_id, batch.request_key, encoded),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("coding batch persistence identity conflict") from exc


def _encode_batch(batch: CodingBatch) -> str:
    document = {
        "schema_version": _SCHEMA_VERSION,
        "batch_id": batch.batch_id,
        "request_key": batch.request_key,
        "repository_id": batch.repository_id,
        "target_ref": batch.target_ref,
        "base_revision": batch.base_revision,
        "aggregation_policy": batch.aggregation_policy.value,
        "concurrency_limit": batch.concurrency_limit,
        "workstreams": [_workstream_json(item) for item in batch.workstreams],
        "overlaps": [
            {
                "left_work_item_id": item.left_work_item_id,
                "right_work_item_id": item.right_work_item_id,
                "kind": item.kind.value,
                "rationale": item.rationale,
            }
            for item in batch.overlaps
        ],
        "integration_candidates": [
            _integration_candidate_json(item) for item in batch.integration_candidates
        ],
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _workstream_json(workstream: CodingWorkstream) -> dict[str, object]:
    item = workstream.work_item
    provenance = workstream.provenance
    result = workstream.result
    verification = workstream.verification
    return {
        "work_item": {
            "work_item_id": item.work_item_id,
            "task_id": item.task_id,
            "plan_id": item.plan_id,
            "step_id": item.step_id,
            "dependencies": list(item.dependencies),
            "affected_paths": list(item.affected_paths),
            "semantic_scopes": list(item.semantic_scopes),
            "metadata": dict(item.metadata),
        },
        "provenance": {
            "task_id": provenance.task_id,
            "plan_id": provenance.plan_id,
            "step_id": provenance.step_id,
            "repository_id": provenance.repository_id,
            "base_revision": provenance.base_revision,
            "agent_revision": provenance.agent_revision,
            "agent_run_id": provenance.agent_run_id,
            "workspace_id": provenance.workspace_id,
            "snapshot_id": provenance.snapshot_id,
            "branch_ref": provenance.branch_ref,
            "output_revision": provenance.output_revision,
        },
        "state": workstream.state.value,
        "blocked_by": list(workstream.blocked_by),
        "result": None
        if result is None
        else {
            "output_revision": result.output_revision,
            "changed_paths": list(result.changed_paths),
            "diff_digest": result.diff_digest,
            "artifact_ids": list(result.artifact_ids),
        },
        "verification": None if verification is None else _verification_json(verification),
        "failure_reason": workstream.failure_reason,
    }


def _conflict_json(conflict: IntegrationConflict) -> dict[str, object]:
    return {
        "kind": conflict.kind.value,
        "workstream_ids": list(conflict.workstream_ids),
        "rationale": conflict.rationale,
    }


def _verification_json(verification: VerificationEvidence) -> dict[str, object]:
    return {
        "verification_id": verification.verification_id,
        "subject_revision": verification.subject_revision,
        "passed": verification.passed,
        "check_refs": list(verification.check_refs),
    }


def _repair_attempt_json(repair: IntegrationRepairAttempt) -> dict[str, object]:
    return {
        "repair_id": repair.repair_id,
        "attempt": repair.attempt,
        "task_id": repair.task_id,
        "plan_id": repair.plan_id,
        "step_id": repair.step_id,
        "target_revision": repair.target_revision,
        "source_blocker_reasons": list(repair.source_blocker_reasons),
        "source_conflicts": [_conflict_json(item) for item in repair.source_conflicts],
        "state": repair.state.value,
        "output_revision": repair.output_revision,
        "verification": None
        if repair.verification is None
        else _verification_json(repair.verification),
        "failure_reason": repair.failure_reason,
    }


def _integration_candidate_json(candidate: IntegrationCandidate) -> dict[str, object]:
    validation = candidate.validation
    return {
        "integration_id": candidate.integration_id,
        "target_base_revision": candidate.target_base_revision,
        "ordered_workstream_ids": list(candidate.ordered_workstream_ids),
        "ordered_revisions": list(candidate.ordered_revisions),
        "state": candidate.state.value,
        "conflicts": [_conflict_json(conflict) for conflict in candidate.conflicts],
        "integrated_revision": candidate.integrated_revision,
        "validation": None
        if validation is None
        else {
            "subject_revision": validation.subject_revision,
            "verification_id": validation.verification_id,
            "tests_passed": validation.tests_passed,
            "required_checks": [
                {
                    "name": check.name,
                    "revision": check.revision,
                    "state": check.state.value,
                    "external_ref": check.external_ref,
                }
                for check in validation.required_checks
            ],
            "evaluation_refs": list(validation.evaluation_refs),
        },
        "stale_base": candidate.stale_base,
        "blocker_reasons": list(candidate.blocker_reasons),
        "repair_attempts": [_repair_attempt_json(item) for item in candidate.repair_attempts],
        "change_request_ref": candidate.change_request_ref,
    }


def _decode_batch(encoded: str) -> CodingBatch:
    raw = cast(dict[str, Any], json.loads(encoded))
    if raw.get("schema_version") != _SCHEMA_VERSION:
        raise ValueError("unsupported persisted coding-batch payload schema version")
    return CodingBatch(
        batch_id=raw["batch_id"],
        request_key=raw["request_key"],
        repository_id=raw["repository_id"],
        target_ref=raw["target_ref"],
        base_revision=raw["base_revision"],
        workstreams=tuple(_decode_workstream(item) for item in raw["workstreams"]),
        overlaps=tuple(
            OverlapDecision(
                left_work_item_id=item["left_work_item_id"],
                right_work_item_id=item["right_work_item_id"],
                kind=OverlapKind(item["kind"]),
                rationale=item["rationale"],
            )
            for item in raw["overlaps"]
        ),
        aggregation_policy=BatchAggregationPolicy(raw["aggregation_policy"]),
        concurrency_limit=raw["concurrency_limit"],
        integration_candidates=tuple(
            _decode_integration_candidate(item) for item in raw["integration_candidates"]
        ),
    )


def _decode_workstream(raw_value: object) -> CodingWorkstream:
    raw = cast(dict[str, Any], raw_value)
    item = cast(dict[str, Any], raw["work_item"])
    provenance = cast(dict[str, Any], raw["provenance"])
    result_raw = raw["result"]
    verification_raw = raw["verification"]
    return CodingWorkstream(
        work_item=CodingWorkItem(
            work_item_id=item["work_item_id"],
            task_id=item["task_id"],
            plan_id=item["plan_id"],
            step_id=item["step_id"],
            dependencies=tuple(item["dependencies"]),
            affected_paths=tuple(item["affected_paths"]),
            semantic_scopes=tuple(item["semantic_scopes"]),
            metadata=cast(dict[str, str], item["metadata"]),
        ),
        provenance=WorkstreamProvenance(
            task_id=provenance["task_id"],
            plan_id=provenance["plan_id"],
            step_id=provenance["step_id"],
            repository_id=provenance["repository_id"],
            base_revision=provenance["base_revision"],
            agent_revision=provenance["agent_revision"],
            agent_run_id=provenance["agent_run_id"],
            workspace_id=provenance["workspace_id"],
            snapshot_id=provenance["snapshot_id"],
            branch_ref=provenance["branch_ref"],
            output_revision=provenance["output_revision"],
        ),
        state=WorkstreamState(raw["state"]),
        blocked_by=tuple(raw["blocked_by"]),
        result=None if result_raw is None else _decode_result(result_raw),
        verification=None if verification_raw is None else _decode_verification(verification_raw),
        failure_reason=raw["failure_reason"],
    )


def _decode_result(raw_value: object) -> WorkstreamResult:
    raw = cast(dict[str, Any], raw_value)
    return WorkstreamResult(
        output_revision=raw["output_revision"],
        changed_paths=tuple(raw["changed_paths"]),
        diff_digest=raw["diff_digest"],
        artifact_ids=tuple(raw["artifact_ids"]),
    )


def _decode_verification(raw_value: object) -> VerificationEvidence:
    raw = cast(dict[str, Any], raw_value)
    return VerificationEvidence(
        verification_id=raw["verification_id"],
        subject_revision=raw["subject_revision"],
        passed=raw["passed"],
        check_refs=tuple(raw["check_refs"]),
    )


def _decode_conflict(raw_value: object) -> IntegrationConflict:
    raw = cast(dict[str, Any], raw_value)
    return IntegrationConflict(
        kind=OverlapKind(raw["kind"]),
        workstream_ids=tuple(raw["workstream_ids"]),
        rationale=raw["rationale"],
    )


def _decode_repair_attempt(raw_value: object) -> IntegrationRepairAttempt:
    raw = cast(dict[str, Any], raw_value)
    verification_raw = raw.get("verification")
    return IntegrationRepairAttempt(
        repair_id=raw["repair_id"],
        attempt=raw["attempt"],
        task_id=raw["task_id"],
        plan_id=raw["plan_id"],
        step_id=raw["step_id"],
        target_revision=raw["target_revision"],
        source_blocker_reasons=tuple(raw["source_blocker_reasons"]),
        source_conflicts=tuple(_decode_conflict(item) for item in raw["source_conflicts"]),
        state=RepairAttemptState(raw["state"]),
        output_revision=raw["output_revision"],
        verification=None if verification_raw is None else _decode_verification(verification_raw),
        failure_reason=raw["failure_reason"],
    )


def _decode_integration_candidate(raw_value: object) -> IntegrationCandidate:
    raw = cast(dict[str, Any], raw_value)
    validation_raw = raw["validation"]
    return IntegrationCandidate(
        integration_id=raw["integration_id"],
        target_base_revision=raw["target_base_revision"],
        ordered_workstream_ids=tuple(raw["ordered_workstream_ids"]),
        ordered_revisions=tuple(raw["ordered_revisions"]),
        state=IntegrationState(raw["state"]),
        conflicts=tuple(_decode_conflict(item) for item in raw["conflicts"]),
        integrated_revision=raw["integrated_revision"],
        validation=None if validation_raw is None else _decode_combined_validation(validation_raw),
        stale_base=raw["stale_base"],
        blocker_reasons=tuple(raw["blocker_reasons"]),
        repair_attempts=tuple(
            _decode_repair_attempt(item) for item in raw.get("repair_attempts", [])
        ),
        change_request_ref=raw["change_request_ref"],
    )


def _decode_combined_validation(raw_value: object) -> CombinedValidationEvidence:
    raw = cast(dict[str, Any], raw_value)
    return CombinedValidationEvidence(
        subject_revision=raw["subject_revision"],
        verification_id=raw["verification_id"],
        tests_passed=raw["tests_passed"],
        required_checks=tuple(
            RequiredCheck(
                name=item["name"],
                revision=item["revision"],
                state=CheckState(item["state"]),
                external_ref=item["external_ref"],
            )
            for item in raw["required_checks"]
        ),
        evaluation_refs=tuple(raw["evaluation_refs"]),
    )
