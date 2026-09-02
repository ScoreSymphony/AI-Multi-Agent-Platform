"""Secret-provider contract and deterministic local reference backend."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import ProviderContract
from ai_multi_agent_platform.contracts.types import HealthStatus, ProviderDescriptor
from ai_multi_agent_platform.security import SecretReference, redact_sensitive


class SecretState(StrEnum):
    ACTIVE = "active"
    REVOKED = "revoked"


@dataclass(frozen=True, slots=True)
class SecretAuditEvent:
    operation: str
    secret_id: str
    consumer_ref: str | None
    outcome: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class SecretAccessContext:
    """Canonical least-privilege context supplied for one secret resolution."""

    consumer_ref: str
    project_id: str | None = None
    workspace_id: str | None = None
    task_id: str | None = None
    run_id: str | None = None
    action: str | None = None
    capability_ref: str | None = None
    purpose: str | None = None
    requested_lifetime_seconds: int = 300

    def __post_init__(self) -> None:
        if not self.consumer_ref.strip():
            raise ValueError("secret consumer_ref must not be blank")
        if self.requested_lifetime_seconds <= 0:
            raise ValueError("requested secret lifetime must be greater than zero")


@dataclass(frozen=True, slots=True)
class SecretMetadata:
    """Value-free metadata for one canonical secret reference."""

    reference: SecretReference
    purpose: str
    created_at: datetime
    updated_at: datetime
    state: SecretState = SecretState.ACTIVE
    expires_at: datetime | None = None
    rotated_at: datetime | None = None
    rotation_count: int = 0
    allowed_consumers: tuple[str, ...] = ()
    allowed_purposes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.purpose.strip():
            raise ValueError("secret purpose must not be blank")

    def to_dict(self) -> dict[str, object]:
        return {
            "reference": _safe_reference_dict(self.reference),
            "purpose": self.purpose,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "state": self.state.value,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "rotated_at": self.rotated_at.isoformat() if self.rotated_at else None,
            "rotation_count": self.rotation_count,
            "allowed_consumers": list(self.allowed_consumers),
            "allowed_purposes": list(self.allowed_purposes),
        }


class SecretMaterial:
    """Short-lived resolved material whose normal representations are always redacted."""

    __slots__ = ("_value", "expires_at")

    def __init__(self, value: str, expires_at: datetime) -> None:
        self._value = value
        self.expires_at = expires_at

    def reveal(self) -> str:
        return self._value

    def __repr__(self) -> str:
        return "SecretMaterial([REDACTED])"

    def __str__(self) -> str:
        return self.__repr__()


class SecretProvider(ProviderContract):
    """Replaceable boundary for secret storage, resolution and lifecycle operations."""

    @abstractmethod
    async def create(
        self,
        reference: SecretReference,
        value: str,
        *,
        purpose: str,
        allowed_consumers: tuple[str, ...] = (),
        allowed_purposes: tuple[str, ...] = (),
        expires_at: datetime | None = None,
    ) -> SecretMetadata: ...

    @abstractmethod
    async def resolve(
        self, reference: SecretReference, context: SecretAccessContext
    ) -> SecretMaterial: ...

    @abstractmethod
    async def rotate(self, reference: SecretReference, value: str) -> SecretMetadata: ...

    @abstractmethod
    async def revoke(self, reference: SecretReference) -> SecretMetadata: ...

    @abstractmethod
    async def delete(self, reference: SecretReference) -> None: ...

    @abstractmethod
    async def metadata(self, reference: SecretReference) -> SecretMetadata: ...


class LocalSecretProvider(SecretProvider):
    """In-process, dependency-free reference backend for tests and minimal deployment.

    Secret material remains only in memory. Nothing in this backend writes plaintext material to
    disk or exposes it through canonical serialization. Durable deployments can replace the
    provider without changing canonical references or callers.
    """

    def __init__(
        self,
        provider_id: str = "local-secrets",
        *,
        available: bool = True,
        audit_hook: Callable[[SecretAuditEvent], None] | None = None,
    ) -> None:
        if not provider_id.strip():
            raise ValueError("secret provider_id must not be blank")
        self._provider_id = provider_id
        self._available = available
        self._audit_hook = audit_hook
        self._values: dict[str, str] = {}
        self._metadata: dict[str, SecretMetadata] = {}

    @property
    def descriptor(self) -> ProviderDescriptor:
        return ProviderDescriptor(
            provider_id=self._provider_id,
            provider_type="secret",
            supported_operations=("create", "resolve", "rotate", "revoke", "delete", "metadata"),
            health=HealthStatus.HEALTHY if self._available else HealthStatus.UNAVAILABLE,
            available=self._available,
        )

    def set_available(self, available: bool) -> None:
        self._available = available

    async def create(
        self,
        reference: SecretReference,
        value: str,
        *,
        purpose: str,
        allowed_consumers: tuple[str, ...] = (),
        allowed_purposes: tuple[str, ...] = (),
        expires_at: datetime | None = None,
    ) -> SecretMetadata:
        self._require_available()
        self._validate_reference_provider(reference)
        if not value:
            raise ContractError(ErrorCode.INVALID_REQUEST, "secret value must not be empty")
        if not purpose.strip():
            raise ContractError(ErrorCode.INVALID_REQUEST, "secret purpose must not be blank")
        if reference.secret_id in self._metadata:
            raise ContractError(ErrorCode.CONFLICT, "secret reference already exists")
        now = datetime.now(UTC)
        metadata = SecretMetadata(
            reference=reference,
            purpose=purpose,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
            allowed_consumers=allowed_consumers,
            allowed_purposes=allowed_purposes,
        )
        self._values[reference.secret_id] = value
        self._metadata[reference.secret_id] = metadata
        self._audit("create", reference.secret_id, None, "success")
        return metadata

    async def resolve(
        self, reference: SecretReference, context: SecretAccessContext
    ) -> SecretMaterial:
        self._require_available()
        metadata = await self.metadata(reference)
        now = datetime.now(UTC)
        if metadata.state is SecretState.REVOKED:
            self._audit("resolve", reference.secret_id, context.consumer_ref, "revoked")
            raise ContractError(ErrorCode.FORBIDDEN, "secret is revoked")
        if metadata.expires_at is not None and metadata.expires_at <= now:
            self._audit("resolve", reference.secret_id, context.consumer_ref, "expired")
            raise ContractError(ErrorCode.FORBIDDEN, "secret is expired")
        if metadata.allowed_consumers and context.consumer_ref not in metadata.allowed_consumers:
            self._audit("resolve", reference.secret_id, context.consumer_ref, "consumer_denied")
            raise ContractError(ErrorCode.FORBIDDEN, "consumer is not allowed to resolve secret")
        requested_purpose = context.purpose or metadata.purpose
        if metadata.allowed_purposes and requested_purpose not in metadata.allowed_purposes:
            self._audit("resolve", reference.secret_id, context.consumer_ref, "purpose_denied")
            raise ContractError(ErrorCode.FORBIDDEN, "purpose is not allowed to resolve secret")
        value = self._values.get(reference.secret_id)
        if value is None:
            self._audit("resolve", reference.secret_id, context.consumer_ref, "missing")
            raise ContractError(ErrorCode.NOT_FOUND, "secret value is missing")
        lifetime = min(context.requested_lifetime_seconds, 3600)
        lease_expiry = now + timedelta(seconds=lifetime)
        if metadata.expires_at is not None:
            lease_expiry = min(lease_expiry, metadata.expires_at)
        self._audit("resolve", reference.secret_id, context.consumer_ref, "success")
        return SecretMaterial(value, lease_expiry)

    async def rotate(self, reference: SecretReference, value: str) -> SecretMetadata:
        self._require_available()
        current = await self.metadata(reference)
        if current.state is SecretState.REVOKED:
            raise ContractError(ErrorCode.FORBIDDEN, "revoked secret cannot be rotated")
        if not value:
            raise ContractError(ErrorCode.INVALID_REQUEST, "secret value must not be empty")
        now = datetime.now(UTC)
        updated = replace(
            current,
            updated_at=now,
            rotated_at=now,
            rotation_count=current.rotation_count + 1,
        )
        self._values[reference.secret_id] = value
        self._metadata[reference.secret_id] = updated
        self._audit("rotate", reference.secret_id, None, "success")
        return updated

    async def revoke(self, reference: SecretReference) -> SecretMetadata:
        self._require_available()
        current = await self.metadata(reference)
        updated = replace(current, state=SecretState.REVOKED, updated_at=datetime.now(UTC))
        self._values.pop(reference.secret_id, None)
        self._metadata[reference.secret_id] = updated
        self._audit("revoke", reference.secret_id, None, "success")
        return updated

    async def delete(self, reference: SecretReference) -> None:
        self._require_available()
        await self.metadata(reference)
        self._values.pop(reference.secret_id, None)
        self._metadata.pop(reference.secret_id, None)
        self._audit("delete", reference.secret_id, None, "success")

    async def metadata(self, reference: SecretReference) -> SecretMetadata:
        self._require_available()
        self._validate_reference_provider(reference)
        metadata = self._metadata.get(reference.secret_id)
        if metadata is None:
            raise ContractError(ErrorCode.NOT_FOUND, "secret reference was not found")
        if metadata.reference != reference:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "secret reference metadata does not match the stored canonical reference",
            )
        return metadata

    def _validate_reference_provider(self, reference: SecretReference) -> None:
        if reference.provider != self._provider_id:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "secret reference provider does not match selected SecretProvider",
            )

    def _audit(
        self, operation: str, secret_id: str, consumer_ref: str | None, outcome: str
    ) -> None:
        if self._audit_hook is not None:
            self._audit_hook(
                SecretAuditEvent(operation, secret_id, consumer_ref, outcome, datetime.now(UTC))
            )

    def _require_available(self) -> None:
        if not self._available:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                "secret backend is unavailable",
                retryable=True,
                provider_id=self._provider_id,
            )


def _safe_reference_dict(reference: SecretReference) -> dict[str, object]:
    redacted = redact_sensitive(reference)
    if not isinstance(redacted, dict):
        raise ValueError("secret reference redaction returned an invalid representation")
    payload = redacted.get("secret_reference")
    if not isinstance(payload, dict):
        raise ValueError("secret reference redaction returned an invalid representation")
    return dict(payload)
