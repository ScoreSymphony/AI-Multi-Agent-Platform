"""Awaitable Authentication service boundary for blocking local persistence."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Protocol, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.persistence_offload import SharedPersistenceOffloadRegistry

from .authentication import (
    AuthenticatedActor,
    BrowserSession,
    IssuedCredential,
    LocalAuthenticationService,
    LocalUserAccount,
    LoginResult,
    MobilePairingGrant,
    PairedMobileDevice,
    SessionGrant,
    StoredCredential,
)
from .authentication_hardening import CredentialScope

_BUSY_MARKERS = (
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
)


class _ScopedAuthenticationService(Protocol):
    def create_personal_access_token(
        self,
        user_id: str,
        *,
        purpose: str,
        expires_at: datetime | None = None,
        now: datetime | None = None,
        scope: CredentialScope | None = None,
    ) -> IssuedCredential: ...

    def check_authenticated_request(
        self,
        actor: AuthenticatedActor,
        *,
        now: datetime | None = None,
    ) -> None: ...


class AsyncAuthenticationService(Protocol):
    """Backend-neutral awaitable Authentication contract for async transports."""

    async def bootstrap_first_admin(
        self,
        username: str,
        password: str,
        *,
        correlation_id: str | None = None,
    ) -> LocalUserAccount: ...

    async def login(
        self,
        username: str,
        password: str,
        *,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> LoginResult: ...

    async def authenticate_session(
        self,
        token: str,
        *,
        csrf_token: str | None = None,
        require_csrf: bool = False,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticatedActor: ...

    async def authenticate_bearer(
        self,
        token: str,
        *,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticatedActor: ...

    async def authenticate_worker_request(
        self,
        token: str,
        *,
        nonce: str,
        issued_at: datetime,
        tls_peer_ref: str | None = None,
        now: datetime | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticatedActor: ...

    async def check_authenticated_request(self, actor: AuthenticatedActor) -> None: ...

    async def logout(self, token: str) -> None: ...

    async def revoke_session(self, user_id: str, session_id: str) -> None: ...

    async def create_browser_session(self, user_id: str) -> SessionGrant: ...

    async def renew_browser_session(self, user_id: str, session_id: str) -> SessionGrant: ...

    async def list_sessions(self, user_id: str) -> tuple[BrowserSession, ...]: ...

    async def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
    ) -> None: ...

    async def list_credentials(self, owner_id: str) -> tuple[StoredCredential, ...]: ...

    async def create_personal_access_token(
        self,
        user_id: str,
        *,
        purpose: str,
        expires_at: datetime | None = None,
        scope: CredentialScope | None = None,
    ) -> IssuedCredential: ...

    async def revoke_credential(self, owner_id: str, credential_id: str) -> None: ...

    async def create_mobile_pairing(
        self,
        user_id: str,
        server_origin: str,
        *,
        correlation_id: str | None = None,
    ) -> MobilePairingGrant: ...

    async def consume_mobile_pairing(
        self,
        pairing_id: str | None,
        code: str,
        *,
        device_name: str,
        platform: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
        protocol_version: str = "1",
        correlation_id: str | None = None,
    ) -> tuple[PairedMobileDevice, IssuedCredential]: ...

    async def cancel_mobile_pairing(
        self,
        user_id: str,
        pairing_id: str,
        *,
        correlation_id: str | None = None,
    ) -> None: ...

    async def list_mobile_devices(self, user_id: str) -> tuple[PairedMobileDevice, ...]: ...

    async def safe_mobile_device(self, device: PairedMobileDevice) -> dict[str, JsonValue]: ...

    async def rename_mobile_device(
        self,
        user_id: str,
        device_id: str,
        display_name: str,
        *,
        correlation_id: str | None = None,
    ) -> PairedMobileDevice: ...

    async def revoke_mobile_device(
        self,
        user_id: str,
        device_id: str,
        *,
        correlation_id: str | None = None,
    ) -> None: ...

    async def revoke_all_mobile_devices(
        self,
        user_id: str,
        *,
        correlation_id: str | None = None,
    ) -> int: ...


class AuthenticationPersistenceOffload:
    """Serialize and bound Authentication persistence outside the asyncio event loop.

    The current local Authentication service combines mutable in-memory indexes with a SQLite
    write-through store. All async-transport operations therefore share one serialization lock so
    the in-memory mutation and its durable SQLite write remain one ordered service operation.
    """

    def __init__(self, *, max_concurrency: int = 2, max_pending: int = 64) -> None:
        if max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")
        if max_pending < max_concurrency:
            raise ValueError("max_pending must be >= max_concurrency")
        self._executor = ThreadPoolExecutor(
            max_workers=max_concurrency,
            thread_name_prefix="authentication-persistence",
        )
        self._service_lock = threading.Lock()
        self._capacity = threading.BoundedSemaphore(max_pending)
        self.max_concurrency = max_concurrency
        self.max_pending = max_pending

    async def run[T](self, operation: Callable[[], T]) -> T:
        if not self._capacity.acquire(blocking=False):
            raise ContractError(
                ErrorCode.TRANSIENT_FAILURE,
                "Authentication persistence queue capacity exhausted",
                retryable=True,
            )
        loop = asyncio.get_running_loop()
        try:
            worker = loop.run_in_executor(self._executor, self._run_sync, operation)
        except BaseException:
            self._capacity.release()
            raise
        return await _await_persistence_boundary(worker)

    def _run_sync[T](self, operation: Callable[[], T]) -> T:
        try:
            with self._service_lock:
                return operation()
        finally:
            self._capacity.release()


_SHARED_AUTHENTICATION_OFFLOADS = SharedPersistenceOffloadRegistry[
    AuthenticationPersistenceOffload
]()


def _authentication_offload(
    service: LocalAuthenticationService,
    requested: AuthenticationPersistenceOffload | None,
    *,
    owner: object,
) -> AuthenticationPersistenceOffload:
    return _SHARED_AUTHENTICATION_OFFLOADS.resolve(
        service,
        owner=owner,
        requested=requested,
        factory=AuthenticationPersistenceOffload,
    )


async def _await_persistence_boundary[T](worker: asyncio.Future[T]) -> T:
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
        failure = worker.exception()
        if failure is not None:
            raise failure from None
        raise


def _sqlite_error(exc: BaseException) -> sqlite3.Error | None:
    current: BaseException | None = exc
    while current is not None:
        if isinstance(current, sqlite3.Error):
            return current
        current = current.__cause__
    return None


def _map_sqlite_error(exc: sqlite3.Error, message: str) -> ContractError:
    if isinstance(exc, sqlite3.OperationalError) and any(
        marker in str(exc).casefold() for marker in _BUSY_MARKERS
    ):
        return ContractError(ErrorCode.TRANSIENT_FAILURE, message, retryable=True)
    return ContractError(ErrorCode.BACKEND_ERROR, message)


class AsyncAuthenticationServiceAdapter:
    """Awaitable facade over the synchronous self-hosted Authentication service."""

    def __init__(
        self,
        service: LocalAuthenticationService,
        *,
        offload: AuthenticationPersistenceOffload | None = None,
    ) -> None:
        self._service = service
        self._offload = _authentication_offload(service, offload, owner=self)

    @property
    def offload(self) -> AuthenticationPersistenceOffload:
        return self._offload

    async def _run[T](self, operation: Callable[[], T], *, message: str) -> T:
        try:
            return await self._offload.run(operation)
        except ContractError as exc:
            sqlite_error = _sqlite_error(exc)
            if sqlite_error is None:
                raise
            raise _map_sqlite_error(sqlite_error, message) from exc
        except sqlite3.Error as exc:
            raise _map_sqlite_error(exc, message) from exc

    async def bootstrap_first_admin(
        self,
        username: str,
        password: str,
        *,
        correlation_id: str | None = None,
    ) -> LocalUserAccount:
        return await self._run(
            lambda: self._service.bootstrap_first_admin(
                username,
                password,
                correlation_id=correlation_id,
            ),
            message="failed to persist bootstrap administrator",
        )

    async def login(
        self,
        username: str,
        password: str,
        *,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> LoginResult:
        return await self._run(
            lambda: self._service.login(
                username,
                password,
                request_id=request_id,
                correlation_id=correlation_id,
            ),
            message="failed to persist authentication login",
        )

    async def authenticate_session(
        self,
        token: str,
        *,
        csrf_token: str | None = None,
        require_csrf: bool = False,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticatedActor:
        return await self._run(
            lambda: self._service.authenticate_session(
                token,
                csrf_token=csrf_token,
                require_csrf=require_csrf,
                request_id=request_id,
                correlation_id=correlation_id,
            ),
            message="failed to persist browser-session authentication",
        )

    async def authenticate_bearer(
        self,
        token: str,
        *,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticatedActor:
        return await self._run(
            lambda: self._service.authenticate_bearer(
                token,
                request_id=request_id,
                correlation_id=correlation_id,
            ),
            message="failed to persist bearer authentication",
        )

    async def authenticate_worker_request(
        self,
        token: str,
        *,
        nonce: str,
        issued_at: datetime,
        tls_peer_ref: str | None = None,
        now: datetime | None = None,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> AuthenticatedActor:
        return await self._run(
            lambda: self._service.authenticate_worker_request(
                token,
                nonce=nonce,
                issued_at=issued_at,
                tls_peer_ref=tls_peer_ref,
                now=now,
                request_id=request_id,
                correlation_id=correlation_id,
            ),
            message="failed to persist worker authentication",
        )

    async def check_authenticated_request(self, actor: AuthenticatedActor) -> None:
        scoped = cast(_ScopedAuthenticationService, self._service)
        await self._run(
            lambda: scoped.check_authenticated_request(actor),
            message="failed to evaluate authenticated request controls",
        )

    async def logout(self, token: str) -> None:
        await self._run(
            lambda: self._service.logout(token),
            message="failed to persist authentication logout",
        )

    async def revoke_session(self, user_id: str, session_id: str) -> None:
        await self._run(
            lambda: self._service.revoke_session(user_id, session_id),
            message="failed to persist session revocation",
        )

    async def create_browser_session(self, user_id: str) -> SessionGrant:
        return await self._run(
            lambda: self._service.create_browser_session(user_id),
            message="failed to persist browser session",
        )

    async def renew_browser_session(self, user_id: str, session_id: str) -> SessionGrant:
        def renew() -> SessionGrant:
            self._service.revoke_session(user_id, session_id)
            return self._service.create_browser_session(user_id)

        return await self._run(
            renew,
            message="failed to persist browser-session renewal",
        )

    async def list_sessions(self, user_id: str) -> tuple[BrowserSession, ...]:
        return await self._run(
            lambda: self._service.list_sessions(user_id),
            message="failed to read browser sessions",
        )

    async def change_password(
        self,
        user_id: str,
        current_password: str,
        new_password: str,
    ) -> None:
        await self._run(
            lambda: self._service.change_password(user_id, current_password, new_password),
            message="failed to persist password change",
        )

    async def list_credentials(self, owner_id: str) -> tuple[StoredCredential, ...]:
        return await self._run(
            lambda: self._service.list_credentials(owner_id),
            message="failed to read authentication credentials",
        )

    async def create_personal_access_token(
        self,
        user_id: str,
        *,
        purpose: str,
        expires_at: datetime | None = None,
        scope: CredentialScope | None = None,
    ) -> IssuedCredential:
        if scope is None:
            return await self._run(
                lambda: self._service.create_personal_access_token(
                    user_id,
                    purpose=purpose,
                    expires_at=expires_at,
                ),
                message="failed to persist personal access token",
            )
        scoped = cast(_ScopedAuthenticationService, self._service)
        return await self._run(
            lambda: scoped.create_personal_access_token(
                user_id,
                purpose=purpose,
                expires_at=expires_at,
                scope=scope,
            ),
            message="failed to persist personal access token",
        )

    async def revoke_credential(self, owner_id: str, credential_id: str) -> None:
        await self._run(
            lambda: self._service.revoke_credential(owner_id, credential_id),
            message="failed to persist credential revocation",
        )

    async def create_mobile_pairing(
        self,
        user_id: str,
        server_origin: str,
        *,
        correlation_id: str | None = None,
    ) -> MobilePairingGrant:
        return await self._run(
            lambda: self._service.mobile_pairing.create_challenge(
                user_id,
                server_origin,
                correlation_id=correlation_id,
            ),
            message="failed to persist mobile pairing challenge",
        )

    async def consume_mobile_pairing(
        self,
        pairing_id: str | None,
        code: str,
        *,
        device_name: str,
        platform: str | None = None,
        metadata: dict[str, JsonValue] | None = None,
        protocol_version: str = "1",
        correlation_id: str | None = None,
    ) -> tuple[PairedMobileDevice, IssuedCredential]:
        return await self._run(
            lambda: self._service.mobile_pairing.consume_challenge(
                pairing_id,
                code,
                device_name=device_name,
                platform=platform,
                metadata=metadata,
                protocol_version=protocol_version,
                correlation_id=correlation_id,
            ),
            message="failed to persist mobile pairing consumption",
        )

    async def cancel_mobile_pairing(
        self,
        user_id: str,
        pairing_id: str,
        *,
        correlation_id: str | None = None,
    ) -> None:
        await self._run(
            lambda: self._service.mobile_pairing.cancel_challenge(
                user_id,
                pairing_id,
                correlation_id=correlation_id,
            ),
            message="failed to persist mobile pairing cancellation",
        )

    async def list_mobile_devices(self, user_id: str) -> tuple[PairedMobileDevice, ...]:
        return await self._run(
            lambda: self._service.mobile_pairing.list_devices(user_id),
            message="failed to read paired mobile devices",
        )

    async def safe_mobile_device(self, device: PairedMobileDevice) -> dict[str, JsonValue]:
        return await self._run(
            lambda: self._service.mobile_pairing.safe_device(device),
            message="failed to read paired mobile device status",
        )

    async def rename_mobile_device(
        self,
        user_id: str,
        device_id: str,
        display_name: str,
        *,
        correlation_id: str | None = None,
    ) -> PairedMobileDevice:
        return await self._run(
            lambda: self._service.mobile_pairing.rename_device(
                user_id,
                device_id,
                display_name,
                correlation_id=correlation_id,
            ),
            message="failed to persist mobile device rename",
        )

    async def revoke_mobile_device(
        self,
        user_id: str,
        device_id: str,
        *,
        correlation_id: str | None = None,
    ) -> None:
        await self._run(
            lambda: self._service.mobile_pairing.revoke_device(
                user_id,
                device_id,
                correlation_id=correlation_id,
            ),
            message="failed to persist mobile device revocation",
        )

    async def revoke_all_mobile_devices(
        self,
        user_id: str,
        *,
        correlation_id: str | None = None,
    ) -> int:
        return await self._run(
            lambda: self._service.mobile_pairing.revoke_all_devices(
                user_id,
                correlation_id=correlation_id,
            ),
            message="failed to persist mobile device revocations",
        )


def runtime_authentication_service(
    service: LocalAuthenticationService,
    *,
    runtime_service: AsyncAuthenticationService | None = None,
) -> AsyncAuthenticationService:
    if runtime_service is not None:
        return runtime_service
    return AsyncAuthenticationServiceAdapter(service)


__all__ = [
    "AsyncAuthenticationService",
    "AsyncAuthenticationServiceAdapter",
    "AuthenticationPersistenceOffload",
    "runtime_authentication_service",
]
