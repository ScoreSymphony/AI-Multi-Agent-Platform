"""Fail-closed active/passive Control Plane leadership service."""

from __future__ import annotations

from datetime import timedelta

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


class ControlPlaneFailoverService:
    """Own one process's optional HA role without owning canonical Task/Run state.

    Single-node mode requires no coordinator and is active immediately. HA modes fail closed:
    authority is granted only after the coordinator validates the process's current fencing token.
    """

    def __init__(
        self,
        *,
        instance_id: str,
        mode: AvailabilityMode,
        coordinator: CoordinationProvider | None = None,
        lease_ttl: timedelta = timedelta(seconds=15),
        reconciler: FailoverReconciler | None = None,
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
            self._role = ControlPlaneRole.ACTIVE
            return True

        coordinator = self._required_coordinator()
        previous_epoch = await self._observed_epoch(coordinator)
        try:
            lease = await coordinator.acquire(self.instance_id, ttl=self.lease_ttl)
        except LeadershipConflict:
            self._lease = None
            self._role = ControlPlaneRole.STANDBY
            return False
        except CoordinationUnavailable:
            self._lease = None
            self._role = ControlPlaneRole.FENCED
            raise

        already_owned = (
            self._lease is not None
            and self._lease.token == lease.token
            and self._role is ControlPlaneRole.ACTIVE
        )
        self._lease = lease

        if already_owned:
            return True

        self._role = ControlPlaneRole.PROMOTING
        self._reconciling = True
        try:
            reconciliation = await self._reconcile(
                token=lease.token,
                previous_epoch=previous_epoch,
                reason=reason,
            )
        except Exception as exc:
            await self._best_effort_release(lease.token)
            self._lease = None
            self._role = ControlPlaneRole.FENCED
            raise PromotionReconciliationError(
                "promotion reconciliation failed; leadership was released"
            ) from exc
        finally:
            self._reconciling = False

        self._last_reconciliation = reconciliation
        self._promotion_count += 1
        self._last_promotion_reason = reason
        self._role = ControlPlaneRole.ACTIVE
        return True

    async def renew(self) -> CoordinationLease | None:
        """Renew leadership or fail closed when current authority cannot be proven."""

        if self.mode is AvailabilityMode.SINGLE_NODE:
            return None
        if self._role is not ControlPlaneRole.ACTIVE or self._lease is None:
            raise NotLeaderError("only the active Control Plane instance can renew leadership")

        coordinator = self._required_coordinator()
        try:
            renewed = await coordinator.renew(self._lease.token, ttl=self.lease_ttl)
        except (CoordinationUnavailable, StaleFencingToken):
            self._lease = None
            self._role = ControlPlaneRole.FENCED
            raise
        self._lease = renewed
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
            raise NotLeaderError("Control Plane instance is not the active leader")
        return await self._grant_current_lease_authority()

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
            raise NotLeaderError("reconciliation authority is not currently available")
        return await self._grant_current_lease_authority()

    async def step_down(self) -> None:
        """Relinquish leadership; stale or unavailable coordination still fails closed."""

        if self.mode is AvailabilityMode.SINGLE_NODE:
            return
        if self._lease is None:
            self._role = ControlPlaneRole.STANDBY
            return

        token = self._lease.token
        coordinator = self._required_coordinator()
        self._lease = None
        self._role = ControlPlaneRole.STANDBY
        try:
            await coordinator.release(token)
        except StaleFencingToken:
            return
        except CoordinationUnavailable:
            self._role = ControlPlaneRole.FENCED
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
            )

        coordinator = self._required_coordinator()
        try:
            state = await coordinator.inspect()
        except CoordinationUnavailable:
            if self._role in {ControlPlaneRole.ACTIVE, ControlPlaneRole.PROMOTING}:
                self._role = ControlPlaneRole.FENCED
                self._lease = None
            return ControlPlaneHAStatus(
                instance_id=self.instance_id,
                mode=self.mode,
                role=self._role,
                leader_instance_id=None,
                epoch=0 if self._lease is None else self._lease.token.epoch,
                lease_expires_at=None,
                coordination_available=False,
                promotion_count=self._promotion_count,
                last_promotion_reason=self._last_promotion_reason,
                last_reconciliation=self._last_reconciliation,
            )

        if (
            self._role in {ControlPlaneRole.ACTIVE, ControlPlaneRole.PROMOTING}
            and self._lease is not None
            and (
                state.owner_instance_id != self.instance_id
                or state.epoch != self._lease.token.epoch
            )
        ):
            self._lease = None
            self._role = ControlPlaneRole.FENCED

        return ControlPlaneHAStatus(
            instance_id=self.instance_id,
            mode=self.mode,
            role=self._role,
            leader_instance_id=state.owner_instance_id,
            epoch=state.epoch,
            lease_expires_at=state.expires_at,
            coordination_available=state.available,
            promotion_count=self._promotion_count,
            last_promotion_reason=self._last_promotion_reason,
            last_reconciliation=self._last_reconciliation,
        )

    async def _grant_current_lease_authority(self) -> AuthorityGrant:
        lease = self._lease
        if lease is None:
            raise NotLeaderError("Control Plane instance has no current leadership lease")
        coordinator = self._required_coordinator()
        try:
            await coordinator.assert_fence(lease.token)
        except (CoordinationUnavailable, StaleFencingToken):
            self._lease = None
            self._role = ControlPlaneRole.FENCED
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
            self._role = ControlPlaneRole.FENCED
            raise

    async def _best_effort_release(self, token: FencingToken) -> None:
        coordinator = self._required_coordinator()
        try:
            await coordinator.release(token)
        except (CoordinationUnavailable, StaleFencingToken):
            return

    def _required_coordinator(self) -> CoordinationProvider:
        if self.coordinator is None:
            raise RuntimeError("HA mode is missing its coordination provider")
        return self.coordinator

    @staticmethod
    def _validate_reason(reason: str) -> None:
        if not reason.strip():
            raise ValueError("promotion reason must not be blank")
