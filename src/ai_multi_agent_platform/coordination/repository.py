"""Replaceable persistence/claim boundary for durable Plan/Step coordination."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import RLock
from typing import Protocol
from uuid import uuid4

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import Plan, Step

from .models import CoordinatorClaim, PlanRuntimeState, StepCoordinationRecord


class CoordinatorRepository(Protocol):
    def create_plan(
        self,
        plan: Plan,
        steps: tuple[Step, ...],
        records: tuple[StepCoordinationRecord, ...],
    ) -> PlanRuntimeState: ...

    def get_plan(self, plan_id: str) -> PlanRuntimeState: ...

    def get_step_record(self, step_id: str) -> StepCoordinationRecord: ...

    def list_step_records(self, plan_id: str) -> tuple[StepCoordinationRecord, ...]: ...

    def list_active_plans(self) -> tuple[PlanRuntimeState, ...]: ...

    def save_step(
        self,
        *,
        step: Step,
        record: StepCoordinationRecord,
        expected_revision: int,
        claim: CoordinatorClaim | None = None,
        now: datetime | None = None,
    ) -> StepCoordinationRecord: ...

    def acquire_claim(
        self,
        *,
        step_id: str,
        owner_id: str,
        ttl: timedelta,
        now: datetime,
    ) -> CoordinatorClaim | None: ...

    def renew_claim(
        self,
        *,
        claim: CoordinatorClaim,
        ttl: timedelta,
        now: datetime,
    ) -> CoordinatorClaim | None: ...

    def release_claim(self, claim: CoordinatorClaim) -> bool: ...


class InMemoryCoordinatorRepository:
    """Deterministic reference store with optimistic revisions and fenced claims."""

    def __init__(self) -> None:
        self._plans: dict[str, PlanRuntimeState] = {}
        self._records: dict[str, StepCoordinationRecord] = {}
        self._claims: dict[str, CoordinatorClaim] = {}
        self._fences: dict[str, int] = {}
        self._lock = RLock()

    def create_plan(
        self,
        plan: Plan,
        steps: tuple[Step, ...],
        records: tuple[StepCoordinationRecord, ...],
    ) -> PlanRuntimeState:
        state = PlanRuntimeState(plan=plan, steps=steps)
        if {record.step_id for record in records} != {step.id for step in steps}:
            raise ValueError("coordination records must cover every canonical Step exactly once")
        with self._lock:
            existing = self._plans.get(plan.id)
            if existing is not None:
                if existing.plan == plan and existing.steps == steps:
                    return existing
                raise ContractError(ErrorCode.CONFLICT, f"plan {plan.id} is already registered")
            self._plans[plan.id] = state
            for record in records:
                self._records[record.step_id] = record
            return state

    def get_plan(self, plan_id: str) -> PlanRuntimeState:
        with self._lock:
            try:
                return self._plans[plan_id]
            except KeyError as exc:
                raise ContractError(
                    ErrorCode.NOT_FOUND, f"coordination plan {plan_id} not found"
                ) from exc

    def get_step_record(self, step_id: str) -> StepCoordinationRecord:
        with self._lock:
            try:
                return self._records[step_id]
            except KeyError as exc:
                raise ContractError(
                    ErrorCode.NOT_FOUND, f"coordination Step {step_id} not found"
                ) from exc

    def list_step_records(self, plan_id: str) -> tuple[StepCoordinationRecord, ...]:
        self.get_plan(plan_id)
        with self._lock:
            return tuple(
                sorted(
                    (record for record in self._records.values() if record.plan_id == plan_id),
                    key=lambda item: item.step_id,
                )
            )

    def list_active_plans(self) -> tuple[PlanRuntimeState, ...]:
        with self._lock:
            return tuple(sorted(self._plans.values(), key=lambda item: item.plan.id))

    def save_step(
        self,
        *,
        step: Step,
        record: StepCoordinationRecord,
        expected_revision: int,
        claim: CoordinatorClaim | None = None,
        now: datetime | None = None,
    ) -> StepCoordinationRecord:
        if step.id != record.step_id or step.plan_id != record.plan_id:
            raise ValueError("canonical Step and coordination record identity do not match")
        commit_time = now or datetime.now(UTC)
        if commit_time.tzinfo is None:
            raise ValueError("coordinator commit time must be timezone-aware")
        with self._lock:
            if claim is not None:
                current_claim = self._claims.get(step.id)
                if current_claim != claim or current_claim.expires_at <= commit_time:
                    raise ContractError(
                        ErrorCode.CONFLICT,
                        "stale or expired coordinator claim",
                        details={"step_id": step.id, "fence": claim.fence},
                    )
            current = self._records.get(step.id)
            if current is None:
                raise ContractError(ErrorCode.NOT_FOUND, f"coordination Step {step.id} not found")
            if current.revision != expected_revision:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    "stale coordinator revision",
                    details={
                        "step_id": step.id,
                        "expected_revision": expected_revision,
                        "current_revision": current.revision,
                    },
                )
            plan_state = self._plans[step.plan_id]
            steps = tuple(step if item.id == step.id else item for item in plan_state.steps)
            saved = replace(record, revision=current.revision + 1, updated_at=commit_time)
            self._records[step.id] = saved
            self._plans[step.plan_id] = replace(
                plan_state,
                steps=steps,
                store_revision=plan_state.store_revision + 1,
            )
            return saved

    def acquire_claim(
        self,
        *,
        step_id: str,
        owner_id: str,
        ttl: timedelta,
        now: datetime,
    ) -> CoordinatorClaim | None:
        if ttl.total_seconds() <= 0:
            raise ValueError("claim ttl must be positive")
        if now.tzinfo is None:
            raise ValueError("claim time must be timezone-aware")
        self.get_step_record(step_id)
        with self._lock:
            current = self._claims.get(step_id)
            if current is not None and current.expires_at > now and current.owner_id != owner_id:
                return None
            fence = self._fences.get(step_id, 0) + 1
            self._fences[step_id] = fence
            claim = CoordinatorClaim(
                step_id=step_id,
                claim_id=f"claim-{uuid4()}",
                owner_id=owner_id,
                fence=fence,
                expires_at=now + ttl,
            )
            self._claims[step_id] = claim
            return claim

    def renew_claim(
        self,
        *,
        claim: CoordinatorClaim,
        ttl: timedelta,
        now: datetime,
    ) -> CoordinatorClaim | None:
        if ttl.total_seconds() <= 0:
            raise ValueError("claim ttl must be positive")
        with self._lock:
            current = self._claims.get(claim.step_id)
            if current != claim or current.expires_at <= now:
                return None
            renewed = replace(current, expires_at=now + ttl)
            self._claims[claim.step_id] = renewed
            return renewed

    def release_claim(self, claim: CoordinatorClaim) -> bool:
        with self._lock:
            current = self._claims.get(claim.step_id)
            if current != claim:
                return False
            del self._claims[claim.step_id]
            return True
