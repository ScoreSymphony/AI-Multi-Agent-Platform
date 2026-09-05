"""Platform-owned contracts for optional Control Plane high availability."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol


class AvailabilityMode(StrEnum):
    """Supported Control Plane availability profiles."""

    SINGLE_NODE = "single_node"
    WARM_STANDBY = "warm_standby"
    ACTIVE_PASSIVE = "active_passive"


class ControlPlaneRole(StrEnum):
    """Operational role of one replaceable Control Plane process."""

    ACTIVE = "active"
    STANDBY = "standby"
    PROMOTING = "promoting"
    FENCED = "fenced"


class CoordinationError(RuntimeError):
    """Base error for replaceable HA coordination providers."""


class CoordinationUnavailable(CoordinationError):
    """Raised when the coordination backend cannot prove current authority."""


class LeadershipConflict(CoordinationError):
    """Raised when another non-expired leader currently owns the lease."""


class StaleFencingToken(CoordinationError):
    """Raised when an authority-bearing operation uses stale leadership evidence."""


class NotLeaderError(CoordinationError):
    """Raised when a standby/fenced instance attempts an authority-bearing operation."""


class PromotionReconciliationError(CoordinationError):
    """Raised when a newly acquired leader cannot reconcile safely before activation."""


@dataclass(frozen=True, slots=True)
class FencingToken:
    """Monotonic leadership generation bound to one operational instance."""

    instance_id: str
    epoch: int

    def __post_init__(self) -> None:
        if not self.instance_id.strip():
            raise ValueError("instance_id must not be blank")
        if self.epoch <= 0:
            raise ValueError("fencing epoch must be positive")


@dataclass(frozen=True, slots=True)
class CoordinationLease:
    """Lease returned by the coordination authority.

    Expiry timestamps are observational. Callers must not decide authority from their local
    wall clock; only the coordination provider may validate a fencing token.
    """

    token: FencingToken
    acquired_at: datetime
    expires_at: datetime

    def __post_init__(self) -> None:
        if self.expires_at <= self.acquired_at:
            raise ValueError("lease expiry must be after acquisition")


@dataclass(frozen=True, slots=True)
class CoordinationState:
    """Current inspectable state of a replaceable coordination provider."""

    epoch: int
    owner_instance_id: str | None
    expires_at: datetime | None
    available: bool = True

    def __post_init__(self) -> None:
        if self.epoch < 0:
            raise ValueError("coordination epoch must not be negative")
        if self.owner_instance_id is None and self.expires_at is not None:
            raise ValueError("leaderless coordination state cannot expose lease expiry")
        if self.owner_instance_id is not None and not self.owner_instance_id.strip():
            raise ValueError("owner_instance_id must not be blank")


class CoordinationProvider(Protocol):
    """Replaceable single-writer lease/fencing authority.

    The provider owns time/expiry decisions. Implementations may use a transactional database,
    etcd-like service, another consensus-backed store, or a deterministic test fixture. Backend
    identifiers never become canonical Task/Run identity.
    """

    async def acquire(self, instance_id: str, *, ttl: timedelta) -> CoordinationLease: ...

    async def renew(self, token: FencingToken, *, ttl: timedelta) -> CoordinationLease: ...

    async def release(self, token: FencingToken) -> None: ...

    async def inspect(self) -> CoordinationState: ...

    async def assert_fence(self, token: FencingToken) -> None: ...


@dataclass(frozen=True, slots=True)
class ReconciliationResult:
    """Outcome of the failover reconciliation barrier before accepting authority."""

    recovered_items: int = 0
    rejected_stale_items: int = 0
    details: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.recovered_items < 0:
            raise ValueError("recovered_items must not be negative")
        if self.rejected_stale_items < 0:
            raise ValueError("rejected_stale_items must not be negative")


class FailoverReconciler(Protocol):
    """Reconcile durable unfinished work after a leadership change."""

    async def reconcile(
        self,
        *,
        token: FencingToken,
        previous_epoch: int,
        reason: str,
    ) -> ReconciliationResult: ...


@dataclass(frozen=True, slots=True)
class AuthorityGrant:
    """Proof returned immediately before an authority-bearing action."""

    instance_id: str
    mode: AvailabilityMode
    fencing_token: FencingToken | None

    def __post_init__(self) -> None:
        if not self.instance_id.strip():
            raise ValueError("instance_id must not be blank")
        if self.mode is AvailabilityMode.SINGLE_NODE and self.fencing_token is not None:
            raise ValueError("single-node authority must not require an HA fencing token")
        if self.mode is not AvailabilityMode.SINGLE_NODE and self.fencing_token is None:
            raise ValueError("HA authority requires a fencing token")


@dataclass(frozen=True, slots=True)
class ControlPlaneHAStatus:
    """Operational HA status suitable for health/observability projection."""

    instance_id: str
    mode: AvailabilityMode
    role: ControlPlaneRole
    leader_instance_id: str | None
    epoch: int
    lease_expires_at: datetime | None
    coordination_available: bool
    promotion_count: int
    last_promotion_reason: str | None
    last_reconciliation: ReconciliationResult | None

    def __post_init__(self) -> None:
        if not self.instance_id.strip():
            raise ValueError("instance_id must not be blank")
        if self.epoch < 0:
            raise ValueError("epoch must not be negative")
        if self.promotion_count < 0:
            raise ValueError("promotion_count must not be negative")
        if self.last_promotion_reason is not None and not self.last_promotion_reason.strip():
            raise ValueError("last_promotion_reason must not be blank")
