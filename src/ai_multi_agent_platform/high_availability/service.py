"""Fail-closed active/passive Control Plane leadership service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from time import perf_counter

from .contracts import (
    AuthorityGrant,
    AvailabilityMode,
    ControlPlaneHAStatus,
    ControlPlaneRole,
    CoordinationLease,
    CoordinationProvider,
    CoordinationUnavailable,
    FailoverReconciler,
    FencingToken,
    LeadershipConflict,
    NotLeaderError,
    PromotionReconciliationError,
    ReconciliationResult,
    StaleFencingToken,
)
from .telemetry import HighAvailabilityTelemetry


def utc_now() -> datetime:
    return datetime.now(UTC)


class ControlPlaneFailoverService:
    """Own one process's optional HA role without owning canonical Task/Run state.

    Single-node mode requires no coordinator and is active immediately. HA modes fail closed:
    authority is granted only after the coordinator validates the process's current fencing token.
    Local clocks are used only for observational timestamps/durations and never prove authority.
    """

    def __init__(
        self,
        *,
        instance_id: str,
        mode: AvailabilityMode,
        coordinator: CoordinationProvider | None = None,
        lease_ttl: timedelta = timedelta(seconds=15),
        reconciler: FailoverReconciler | None = None,
        telemetry: HighAvailabilityTelemetry | None = None,
        observation_clock: Callable[[], datetime] = utc_now,
        monotonic: Callable[[], float] = perf_counter,
    ) -> None:
        if not instance_id.strip():
            raise ValueError("instance_id must not be blank")
        if lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be positive")
        if mode is AvailabilityMode.SINGLE_NODE and coordinator is not None:
            raise ValueError("single-node mode must not require HA coordination")
        if mode is not AvailabilityMode.SINGLE_NODE and coordinator is None:
            raise ValueError("HA modes require a coordination provider")

        self.instance_id = instance_id
        self.mode = mode
        self.coordinator = coordinator
        self.lease_ttl = lease_ttl
        self.reconciler = reconciler
        self.telemetry = telemetry
        self._observation_clock = observation_clock
        self._monotonic = monotonic
        self._lease: CoordinationLease | None = None
        self._role = (
            ControlPlaneRole.ACTIVE
            if mode is AvailabilityMode.SINGLE_NODE
            else ControlPlaneRole.STANDBY
        )
        self._reconciling = False
        self._promotion_count = 0
        self._last_promotion_reason: str | None = None
        self._last_reconciliation: ReconciliationResult | None = None
        self._last_renewed_at: datetime | None = None
        self._last_error_code: str | None = None

    @property
    def role(self) -> ControlPlaneRole:
        return self._role

    @property
    def fencing_token(self) -> FencingToken | None:
        return None if self._lease is None else self._lease.token

    async def start(self, *, reason: str = "startup") -> bool:
        """Start this process's availability role.

        Returns ``True`` when this instance is active after startup. A leadership conflict is an
        ordinary standby outcome, not an exception.
        """

        self._validate_reason(reason)
        if self.mode is AvailabilityMode.SINGLE_NODE:
            return True
        return await self.try_promote(reason=reason)

    async def try_promote(self, *, reason: str) -> bool:
        """Attempt deterministic promotion; return False while another valid leader exists."""

        self._validate_reason(reason)
        if self.mode is AvailabilityMode.SINGLE_NODE:
            self._set_role(ControlPlaneRole.ACTIVE, reason="single_node")
            return True

        started = self._monotonic()
        coordinator = self._required_coordinator()
        try:
            previous_epoch = await self._observed_epoch(coordinator)
        except CoordinationUnavailable:
            self._promotion_failed(
                reason=reason,
                epoch=self._current_epoch(),
                failure_code="coordination_unavailable",
                retryable=True,
                started=started,
            )
            raise

        if self.telemetry is not None:
            self.telemetry.promotion_started(
                instance_id=self.instance_id,
                mode=self.mode,
                role=self._role,
                previous_epoch=previous_epoch,
                reason=reason,
            )
        try:
            lease = await coordinator.acquire(self.instance_id, ttl=self.lease_ttl)
        except LeadershipConflict:
            self._lease = None
            self._set_role(
                ControlPlaneRole.STANDBY,
                reason="leadership_conflict",
                epoch=previous_epoch,
            )
            if self.telemetry is not None:
                self.telemetry.promotion_conflict(
                    instance_id=self.instance_id,
                    mode=self.mode,
                    role=self._role,
                    previous_epoch=previous_epoch,
                    reason=reason,
                    duration_seconds=self._duration_since(started),
                )
            return False
        except CoordinationUnavailable:
            self._lease = None
            self._last_error_code = "coordination_unavailable"
            self._set_role(
                ControlPlaneRole.FENCED,
                reason="coordination_unavailable",
                epoch=previous_epoch,
            )
            self._coordination_failure(
                operation="acquire",
                failure_code="coordination_unavailable",
                epoch=previous_epoch,
            )
            self._promotion_failed(
                reason=reason,
                epoch=previous_epoch,
                failure_code="coordination_unavailable",
                retryable=True,
                started=started,
            )
            raise

        already_owned = (
            self._lease is not None
            and self._lease.token == lease.token
            and self._role is ControlPlaneRole.ACTIVE
        )
        self._lease = lease

        if already_owned:
            return True

        self._set_role(
            ControlPlaneRole.PROMOTING,
            reason="promotion_lease_acquired",
            epoch=lease.token.epoch,
        )
        self._reconciling = True
        try:
            reconciliation = await self._reconcile(
                token=lease.token,
                previous_epoch=previous_epoch,
                reason=reason,
            )
        except Exception as exc:
            await self._best_effort_release(lease.token)
            self._last_error_code = "promotion_reconciliation_failed"
            self._lease = None
            self._set_role(
                ControlPlaneRole.FENCED,
                reason="promotion_reconciliation_failed",
                epoch=lease.token.epoch,
            )
            self._promotion_failed(
                reason=reason,
                epoch=lease.token.epoch,
                failure_code="promotion_reconciliation_failed",
                retryable=True,
                started=started,
            )
            raise PromotionReconciliationError(
                "promotion reconciliation failed; leadership was released"
            ) from exc
        finally:
            self._reconciling = False

        self._last_reconciliation = reconciliation
        self._promotion_count += 1
        self._last_promotion_reason = reason
        self._last_error_code = None
        self._set_role(
            ControlPlaneRole.ACTIVE,
            reason="promotion_completed",
            epoch=lease.token.epoch,
        )
        if self.telemetry is not None:
            self.telemetry.promotion_completed(
                instance_id=self.instance_id,
                mode=self.mode,
                role=self._role,
                epoch=lease.token.epoch,
                reason=reason,
                duration_seconds=self._duration_since(started),
                reconciliation=reconciliation,
            )
        return True

    async def renew(self) -> CoordinationLease | None:
        """Renew leadership or fail closed when current authority cannot be proven."""

        if self.mode is AvailabilityMode.SINGLE_NODE:
            return None
        if self._role is not ControlPlaneRole.ACTIVE or self._lease is None:
            self._authority_rejected(operation="renew", failure_code="not_leader", retryable=True)
            raise NotLeaderError("only the active Control Plane instance can renew leadership")

        coordinator = self._required_coordinator()
        token = self._lease.token
        try:
            renewed = await coordinator.renew(token, ttl=self.lease_ttl)
        except CoordinationUnavailable:
            self._last_error_code = "coordination_unavailable"
            self._lease = None
            self._set_role(
                ControlPlaneRole.FENCED,
                reason="renew_coordination_unavailable",
                epoch=token.epoch,
            )
            self._coordination_failure(
                operation="renew",
                failure_code="coordination_unavailable",
                epoch=token.epoch,
            )
            raise
        except StaleFencingToken:
            self._last_error_code = "stale_fencing_token"
            self._lease = None
            self._set_role(
                ControlPlaneRole.FENCED,
                reason="renew_stale_fencing_token",
                epoch=token.epoch,
            )
            self._authority_rejected(
                operation="renew",
                failure_code="stale_fencing_token",
                retryable=False,
                epoch=token.epoch,
            )
            raise
        self._lease = renewed
        self._last_renewed_at = self._observation_clock()
        self._last_error_code = None
        if self.telemetry is not None:
            self.telemetry.lease_renewed(
                instance_id=self.instance_id,
                mode=self.mode,
                role=self._role,
                epoch=renewed.token.epoch,
            )
        return renewed

    async def require_authority(self) -> AuthorityGrant:
        """Validate leadership immediately before an authority-bearing operation."""

        if self.mode is AvailabilityMode.SINGLE_NODE:
            return AuthorityGrant(
                instance_id=self.instance_id,
                mode=self.mode,
                fencing_token=None,
            )
        if self._role is not ControlPlaneRole.ACTIVE or self._lease is None:
            self._authority_rejected(
                operation="require_authority",
                failure_code="not_leader",
                retryable=True,
            )
            raise NotLeaderError("Control Plane instance is not the active leader")
        return await self._grant_current_lease_authority(operation="require_authority")

    async def require_reconciliation_authority(self) -> AuthorityGrant:
        """Validate the narrow authority available only inside the promotion barrier.

        This grant exists so reconciliation may query/cancel stale Worker ownership after the
        candidate has acquired a fencing epoch but before public write/dispatch authority is
        enabled. It must not be used for ordinary API commands, Automation ticks or new dispatch.
        """

        if self.mode is AvailabilityMode.SINGLE_NODE:
            return await self.require_authority()
        if self._role is ControlPlaneRole.ACTIVE:
            return await self.require_authority()
        if (
            self._role is not ControlPlaneRole.PROMOTING
            or not self._reconciling
            or self._lease is None
        ):
            self._authority_rejected(
                operation="require_reconciliation_authority",
                failure_code="reconciliation_authority_unavailable",
                retryable=True,
            )
            raise NotLeaderError("reconciliation authority is not currently available")
        return await self._grant_current_lease_authority(
            operation="require_reconciliation_authority"
        )

    async def step_down(self) -> None:
        """Relinquish leadership; stale or unavailable coordination still fails closed."""

        if self.mode is AvailabilityMode.SINGLE_NODE:
            return
        if self._lease is None:
            self._set_role(ControlPlaneRole.STANDBY, reason="step_down_without_lease")
            return

        token = self._lease.token
        coordinator = self._required_coordinator()
        self._lease = None
        self._set_role(
            ControlPlaneRole.STANDBY,
            reason="step_down",
            epoch=token.epoch,
        )
        try:
            await coordinator.release(token)
        except StaleFencingToken:
            return
        except CoordinationUnavailable:
            self._last_error_code = "coordination_unavailable"
            self._set_role(
                ControlPlaneRole.FENCED,
                reason="step_down_coordination_unavailable",
                epoch=token.epoch,
            )
            self._coordination_failure(
                operation="release",
                failure_code="coordination_unavailable",
                epoch=token.epoch,
            )
            raise

    async def status(self) -> ControlPlaneHAStatus:
        """Return operational status without making process identity canonical domain state."""

        if self.mode is AvailabilityMode.SINGLE_NODE:
            return ControlPlaneHAStatus(
                instance_id=self.instance_id,
                mode=self.mode,
                role=ControlPlaneRole.ACTIVE,
                leader_instance_id=self.instance_id,
                epoch=0,
                lease_expires_at=None,
                coordination_available=True,
                promotion_count=0,
                last_promotion_reason=None,
                last_reconciliation=None,
                reconciliation_in_progress=False,
                last_error_code=self._last_error_code,
            )

        coordinator = self._required_coordinator()
        try:
            state = await coordinator.inspect()
        except CoordinationUnavailable:
            previous_error = self._last_error_code
            self._last_error_code = "coordination_unavailable"
            epoch = self._current_epoch()
            if self._role in {ControlPlaneRole.ACTIVE, ControlPlaneRole.PROMOTING}:
                self._lease = None
                self._set_role(
                    ControlPlaneRole.FENCED,
                    reason="status_coordination_unavailable",
                    epoch=epoch,
                )
            if previous_error != self._last_error_code:
                self._coordination_failure(
                    operation="inspect",
                    failure_code="coordination_unavailable",
                    epoch=epoch,
                )
            return self._status_snapshot(
                leader_instance_id=None,
                epoch=epoch,
                lease_expires_at=None,
                coordination_available=False,
            )

        if (
            self._role in {ControlPlaneRole.ACTIVE, ControlPlaneRole.PROMOTING}
            and self._lease is not None
            and (
                state.owner_instance_id != self.instance_id
                or state.epoch != self._lease.token.epoch
            )
        ):
            stale_epoch = self._lease.token.epoch
            self._last_error_code = "leadership_state_mismatch"
            self._lease = None
            self._set_role(
                ControlPlaneRole.FENCED,
                reason="leadership_state_mismatch",
                epoch=stale_epoch,
            )
            self._authority_rejected(
                operation="inspect",
                failure_code="leadership_state_mismatch",
                retryable=False,
                epoch=stale_epoch,
            )

        return self._status_snapshot(
            leader_instance_id=state.owner_instance_id,
            epoch=state.epoch,
            lease_expires_at=state.expires_at,
            coordination_available=state.available,
        )

    async def _grant_current_lease_authority(self, *, operation: str) -> AuthorityGrant:
        lease = self._lease
        if lease is None:
            self._authority_rejected(
                operation=operation,
                failure_code="missing_leadership_lease",
                retryable=True,
            )
            raise NotLeaderError("Control Plane instance has no current leadership lease")
        coordinator = self._required_coordinator()
        try:
            await coordinator.assert_fence(lease.token)
        except CoordinationUnavailable:
            self._last_error_code = "coordination_unavailable"
            self._lease = None
            self._set_role(
                ControlPlaneRole.FENCED,
                reason=f"{operation}_coordination_unavailable",
                epoch=lease.token.epoch,
            )
            self._coordination_failure(
                operation=operation,
                failure_code="coordination_unavailable",
                epoch=lease.token.epoch,
            )
            self._authority_rejected(
                operation=operation,
                failure_code="coordination_unavailable",
                retryable=True,
                epoch=lease.token.epoch,
            )
            raise
        except StaleFencingToken:
            self._last_error_code = "stale_fencing_token"
            self._lease = None
            self._set_role(
                ControlPlaneRole.FENCED,
                reason=f"{operation}_stale_fencing_token",
                epoch=lease.token.epoch,
            )
            self._authority_rejected(
                operation=operation,
                failure_code="stale_fencing_token",
                retryable=False,
                epoch=lease.token.epoch,
            )
            raise
        return AuthorityGrant(
            instance_id=self.instance_id,
            mode=self.mode,
            fencing_token=lease.token,
        )

    async def _reconcile(
        self,
        *,
        token: FencingToken,
        previous_epoch: int,
        reason: str,
    ) -> ReconciliationResult:
        if self.reconciler is None:
            return ReconciliationResult()
        return await self.reconciler.reconcile(
            token=token,
            previous_epoch=previous_epoch,
            reason=reason,
        )

    async def _observed_epoch(self, coordinator: CoordinationProvider) -> int:
        try:
            return (await coordinator.inspect()).epoch
        except CoordinationUnavailable:
            self._last_error_code = "coordination_unavailable"
            epoch = self._current_epoch()
            self._set_role(
                ControlPlaneRole.FENCED,
                reason="inspect_coordination_unavailable",
                epoch=epoch,
            )
            self._coordination_failure(
                operation="inspect",
                failure_code="coordination_unavailable",
                epoch=epoch,
            )
            raise

    async def _best_effort_release(self, token: FencingToken) -> None:
        coordinator = self._required_coordinator()
        try:
            await coordinator.release(token)
        except (CoordinationUnavailable, StaleFencingToken):
            return

    def _status_snapshot(
        self,
        *,
        leader_instance_id: str | None,
        epoch: int,
        lease_expires_at: datetime | None,
        coordination_available: bool,
    ) -> ControlPlaneHAStatus:
        now = self._observation_clock()
        lease = self._lease
        lease_acquired_at = None if lease is None else lease.acquired_at
        lease_age_seconds = (
            None
            if lease_acquired_at is None
            else max(0.0, (now - lease_acquired_at).total_seconds())
        )
        seconds_since_last_renewal = (
            None
            if self._last_renewed_at is None
            else max(0.0, (now - self._last_renewed_at).total_seconds())
        )
        return ControlPlaneHAStatus(
            instance_id=self.instance_id,
            mode=self.mode,
            role=self._role,
            leader_instance_id=leader_instance_id,
            epoch=epoch,
            lease_expires_at=lease_expires_at,
            coordination_available=coordination_available,
            promotion_count=self._promotion_count,
            last_promotion_reason=self._last_promotion_reason,
            last_reconciliation=self._last_reconciliation,
            lease_acquired_at=lease_acquired_at,
            last_renewed_at=self._last_renewed_at,
            lease_age_seconds=lease_age_seconds,
            seconds_since_last_renewal=seconds_since_last_renewal,
            reconciliation_in_progress=self._reconciling,
            last_error_code=self._last_error_code,
        )

    def _set_role(
        self,
        role: ControlPlaneRole,
        *,
        reason: str,
        epoch: int | None = None,
    ) -> None:
        previous = self._role
        self._role = role
        if previous is role or self.telemetry is None:
            return
        self.telemetry.role_changed(
            instance_id=self.instance_id,
            mode=self.mode,
            previous_role=previous,
            current_role=role,
            epoch=self._current_epoch() if epoch is None else epoch,
            reason=reason,
        )

    def _authority_rejected(
        self,
        *,
        operation: str,
        failure_code: str,
        retryable: bool,
        epoch: int | None = None,
    ) -> None:
        if self.telemetry is None:
            return
        self.telemetry.authority_rejected(
            instance_id=self.instance_id,
            mode=self.mode,
            role=self._role,
            epoch=self._current_epoch() if epoch is None else epoch,
            operation=operation,
            failure_code=failure_code,
            retryable=retryable,
        )

    def _coordination_failure(
        self,
        *,
        operation: str,
        failure_code: str,
        epoch: int,
    ) -> None:
        if self.telemetry is None:
            return
        self.telemetry.coordination_failure(
            instance_id=self.instance_id,
            mode=self.mode,
            role=self._role,
            epoch=epoch,
            operation=operation,
            failure_code=failure_code,
        )

    def _promotion_failed(
        self,
        *,
        reason: str,
        epoch: int,
        failure_code: str,
        retryable: bool,
        started: float,
    ) -> None:
        if self.telemetry is None:
            return
        self.telemetry.promotion_failed(
            instance_id=self.instance_id,
            mode=self.mode,
            role=self._role,
            epoch=epoch,
            reason=reason,
            failure_code=failure_code,
            retryable=retryable,
            duration_seconds=self._duration_since(started),
        )

    def _current_epoch(self) -> int:
        return 0 if self._lease is None else self._lease.token.epoch

    def _duration_since(self, started: float) -> float:
        return max(0.0, self._monotonic() - started)

    def _required_coordinator(self) -> CoordinationProvider:
        if self.coordinator is None:
            raise RuntimeError("HA mode is missing its coordination provider")
        return self.coordinator

    @staticmethod
    def _validate_reason(reason: str) -> None:
        if not reason.strip():
            raise ValueError("promotion reason must not be blank")
