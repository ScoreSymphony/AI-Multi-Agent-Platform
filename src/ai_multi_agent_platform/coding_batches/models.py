"""Canonical read/state models for conflict-aware parallel coding batches.

Issue #872 is an orchestration layer over existing planning, workspace, repository,
Agent runtime, verification and authorization authorities.  The models here deliberately
store only composition/provenance state; they do not create a second scheduler, source-
control history, Workspace identity or Verification result model.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Mapping


def _required(value: str, field_name: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{field_name} must not be blank")
    return normalized


def _unique(values: tuple[str, ...], field_name: str) -> tuple[str, ...]:
    normalized = tuple(_required(value, field_name) for value in values)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} values must be unique")
    return normalized


def _freeze_mapping(value: Mapping[str, str]) -> Mapping[str, str]:
    return MappingProxyType(dict(value))


class BatchAggregationPolicy(StrEnum):
    ALL_REQUIRED = "all_required"
    BEST_EFFORT = "best_effort"
    DEPENDENCY_CLOSED = "dependency_closed"


class WorkstreamState(StrEnum):
    PLANNED = "planned"
    READY = "ready"
    BLOCKED = "blocked"
    MATERIALIZED = "materialized"
    RUNNING = "running"
    PRODUCED = "produced"
    VERIFIED = "verified"
    ACCEPTED = "accepted"
    INTEGRATED = "integrated"
    CONFLICT_BLOCKED = "conflict_blocked"
    FAILED = "failed"
    CANCELLED = "cancelled"


class OverlapKind(StrEnum):
    """Conservative relationship between two coding work items."""

    INDEPENDENT = "independent"
    DEPENDENCY = "dependency"
    TEXTUAL_CONFLICT = "textual_conflict"
    SEMANTIC_OVERLAP = "semantic_overlap"
    UNKNOWN = "unknown"


class IntegrationState(StrEnum):
    DRAFT = "draft"
    BLOCKED = "blocked"
    READY = "ready"
    VALIDATING = "validating"
    VALIDATED = "validated"
    MERGE_READY = "merge_ready"
    MERGED = "merged"


class CheckState(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    PENDING = "pending"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class CodingWorkItem:
    """Planner-owned work identity plus overlap hints consumed by #872."""

    work_item_id: str
    task_id: str
    plan_id: str
    step_id: str
    dependencies: tuple[str, ...] = ()
    affected_paths: tuple[str, ...] = ()
    semantic_scopes: tuple[str, ...] = ()
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "work_item_id", _required(self.work_item_id, "work_item_id"))
        object.__setattr__(self, "task_id", _required(self.task_id, "task_id"))
        object.__setattr__(self, "plan_id", _required(self.plan_id, "plan_id"))
        object.__setattr__(self, "step_id", _required(self.step_id, "step_id"))
        object.__setattr__(self, "dependencies", _unique(self.dependencies, "dependency"))
        object.__setattr__(self, "affected_paths", _unique(self.affected_paths, "affected_path"))
        object.__setattr__(self, "semantic_scopes", _unique(self.semantic_scopes, "semantic_scope"))
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))
        if self.work_item_id in self.dependencies:
            raise ValueError("work item cannot depend on itself")


@dataclass(frozen=True, slots=True)
class OverlapDecision:
    left_work_item_id: str
    right_work_item_id: str
    kind: OverlapKind
    rationale: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "left_work_item_id", _required(self.left_work_item_id, "left_work_item_id")
        )
        object.__setattr__(
            self, "right_work_item_id", _required(self.right_work_item_id, "right_work_item_id")
        )
        object.__setattr__(self, "rationale", _required(self.rationale, "rationale"))
        if self.left_work_item_id == self.right_work_item_id:
            raise ValueError("overlap decision requires two distinct work items")

    @property
    def blocks_parallel_start(self) -> bool:
        return self.kind is not OverlapKind.INDEPENDENT


@dataclass(frozen=True, slots=True)
class WorkstreamProvenance:
    """Exact canonical chain plus provider-local branch metadata.

    ``branch_ref`` is intentionally non-canonical metadata.  Workspace/Snapshot and
    repository/base revision remain the durable identities used for recovery.
    """

    task_id: str
    plan_id: str
    step_id: str
    repository_id: str
    base_revision: str
    agent_revision: str | None = None
    agent_run_id: str | None = None
    workspace_id: str | None = None
    snapshot_id: str | None = None
    branch_ref: str | None = None
    output_revision: str | None = None

    def __post_init__(self) -> None:
        for field_name in ("task_id", "plan_id", "step_id", "repository_id", "base_revision"):
            object.__setattr__(self, field_name, _required(getattr(self, field_name), field_name))
        for field_name in (
            "agent_revision",
            "agent_run_id",
            "workspace_id",
            "snapshot_id",
            "branch_ref",
            "output_revision",
        ):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _required(value, field_name))


@dataclass(frozen=True, slots=True)
class WorkstreamResult:
    output_revision: str
    changed_paths: tuple[str, ...]
    diff_digest: str
    artifact_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "output_revision", _required(self.output_revision, "output_revision")
        )
        object.__setattr__(self, "changed_paths", _unique(self.changed_paths, "changed_path"))
        object.__setattr__(self, "diff_digest", _required(self.diff_digest, "diff_digest"))
        object.__setattr__(self, "artifact_ids", _unique(self.artifact_ids, "artifact_id"))


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    verification_id: str
    subject_revision: str
    passed: bool
    check_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "verification_id", _required(self.verification_id, "verification_id")
        )
        object.__setattr__(
            self, "subject_revision", _required(self.subject_revision, "subject_revision")
        )
        object.__setattr__(self, "check_refs", _unique(self.check_refs, "check_ref"))


@dataclass(frozen=True, slots=True)
class CodingWorkstream:
    work_item: CodingWorkItem
    provenance: WorkstreamProvenance
    state: WorkstreamState = WorkstreamState.PLANNED
    blocked_by: tuple[str, ...] = ()
    result: WorkstreamResult | None = None
    verification: VerificationEvidence | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "blocked_by", _unique(self.blocked_by, "blocked_by"))
        if self.provenance.task_id != self.work_item.task_id:
            raise ValueError("workstream task provenance mismatch")
        if self.provenance.plan_id != self.work_item.plan_id:
            raise ValueError("workstream plan provenance mismatch")
        if self.provenance.step_id != self.work_item.step_id:
            raise ValueError("workstream step provenance mismatch")
        if self.failure_reason is not None:
            object.__setattr__(
                self, "failure_reason", _required(self.failure_reason, "failure_reason")
            )

    @property
    def id(self) -> str:
        return self.work_item.work_item_id

    def with_state(self, state: WorkstreamState, **changes: object) -> "CodingWorkstream":
        return replace(self, state=state, **changes)


@dataclass(frozen=True, slots=True)
class RequiredCheck:
    name: str
    revision: str
    state: CheckState
    external_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", _required(self.name, "check name"))
        object.__setattr__(self, "revision", _required(self.revision, "check revision"))
        if self.external_ref is not None:
            object.__setattr__(self, "external_ref", _required(self.external_ref, "external_ref"))


@dataclass(frozen=True, slots=True)
class CombinedValidationEvidence:
    subject_revision: str
    verification_id: str
    tests_passed: bool
    required_checks: tuple[RequiredCheck, ...] = ()
    evaluation_refs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "subject_revision", _required(self.subject_revision, "subject_revision")
        )
        object.__setattr__(
            self, "verification_id", _required(self.verification_id, "verification_id")
        )
        object.__setattr__(self, "evaluation_refs", _unique(self.evaluation_refs, "evaluation_ref"))

    @property
    def passes(self) -> bool:
        return self.tests_passed and all(
            check.state is CheckState.PASS for check in self.required_checks
        )


@dataclass(frozen=True, slots=True)
class IntegrationConflict:
    kind: OverlapKind
    workstream_ids: tuple[str, ...]
    rationale: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "workstream_ids", _unique(self.workstream_ids, "workstream_id"))
        object.__setattr__(self, "rationale", _required(self.rationale, "rationale"))
        if len(self.workstream_ids) < 2:
            raise ValueError("integration conflict must reference at least two workstreams")
        if self.kind is OverlapKind.INDEPENDENT:
            raise ValueError("independent workstreams are not an integration conflict")


@dataclass(frozen=True, slots=True)
class IntegrationCandidate:
    integration_id: str
    target_base_revision: str
    ordered_workstream_ids: tuple[str, ...]
    ordered_revisions: tuple[str, ...]
    state: IntegrationState = IntegrationState.DRAFT
    conflicts: tuple[IntegrationConflict, ...] = ()
    integrated_revision: str | None = None
    validation: CombinedValidationEvidence | None = None
    stale_base: bool = False
    blocker_reasons: tuple[str, ...] = ()
    change_request_ref: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "integration_id", _required(self.integration_id, "integration_id"))
        object.__setattr__(
            self,
            "target_base_revision",
            _required(self.target_base_revision, "target_base_revision"),
        )
        object.__setattr__(
            self, "ordered_workstream_ids", _unique(self.ordered_workstream_ids, "workstream_id")
        )
        object.__setattr__(self, "ordered_revisions", _unique(self.ordered_revisions, "revision"))
        if len(self.ordered_workstream_ids) != len(self.ordered_revisions):
            raise ValueError("integration workstreams/revisions must have equal length")
        object.__setattr__(self, "blocker_reasons", _unique(self.blocker_reasons, "blocker_reason"))
        for field_name in ("integrated_revision", "change_request_ref"):
            value = getattr(self, field_name)
            if value is not None:
                object.__setattr__(self, field_name, _required(value, field_name))


@dataclass(frozen=True, slots=True)
class CodingBatch:
    batch_id: str
    request_key: str
    repository_id: str
    target_ref: str
    base_revision: str
    workstreams: tuple[CodingWorkstream, ...]
    overlaps: tuple[OverlapDecision, ...]
    aggregation_policy: BatchAggregationPolicy = BatchAggregationPolicy.ALL_REQUIRED
    concurrency_limit: int = 4
    integration_candidates: tuple[IntegrationCandidate, ...] = ()

    def __post_init__(self) -> None:
        for field_name in (
            "batch_id",
            "request_key",
            "repository_id",
            "target_ref",
            "base_revision",
        ):
            object.__setattr__(self, field_name, _required(getattr(self, field_name), field_name))
        if self.concurrency_limit < 1:
            raise ValueError("concurrency_limit must be positive")
        ids = tuple(workstream.id for workstream in self.workstreams)
        if len(ids) != len(set(ids)):
            raise ValueError("workstream ids must be unique")
        integration_ids = tuple(item.integration_id for item in self.integration_candidates)
        if len(integration_ids) != len(set(integration_ids)):
            raise ValueError("integration candidate ids must be unique")

    def workstream(self, workstream_id: str) -> CodingWorkstream:
        for workstream in self.workstreams:
            if workstream.id == workstream_id:
                return workstream
        raise KeyError(workstream_id)

    def integration_candidate(self, integration_id: str) -> IntegrationCandidate:
        for candidate in self.integration_candidates:
            if candidate.integration_id == integration_id:
                return candidate
        raise KeyError(integration_id)
