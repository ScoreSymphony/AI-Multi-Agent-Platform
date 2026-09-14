"""Awaitable Worker credential lifecycle over the shared Authentication boundary."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from .async_authentication import AsyncAuthenticationServiceAdapter
from .authentication import IssuedCredential, StoredCredential
from .authentication_hardening import (
    CredentialRotation,
    CredentialScope,
    LocalAuthenticationService,
)


class AsyncWorkerCredentialService(Protocol):
    """Backend-neutral Worker credential operations used by async deployment administration."""

    async def list_credentials(self, owner_id: str) -> tuple[StoredCredential, ...]: ...

    async def create_worker_credential(
        self,
        worker_id: str,
        *,
        purpose: str = "worker authentication",
        expires_at: datetime | None = None,
        now: datetime | None = None,
        scope: CredentialScope | None = None,
    ) -> IssuedCredential: ...

    async def rotate_worker_credential(
        self,
        worker_id: str,
        credential_id: str,
        *,
        purpose: str | None = None,
        expires_at: datetime | None = None,
        scope: CredentialScope | None = None,
        now: datetime | None = None,
    ) -> CredentialRotation: ...


class AsyncWorkerCredentialServiceAdapter(AsyncAuthenticationServiceAdapter):
    """Worker-lifecycle facade sharing the canonical Authentication offload."""

    def __init__(self, service: LocalAuthenticationService) -> None:
        super().__init__(service)
        self._worker_service = service

    async def create_worker_credential(
        self,
        worker_id: str,
        *,
        purpose: str = "worker authentication",
        expires_at: datetime | None = None,
        now: datetime | None = None,
        scope: CredentialScope | None = None,
    ) -> IssuedCredential:
        return await self._run(
            lambda: self._worker_service.create_worker_credential(
                worker_id,
                purpose=purpose,
                expires_at=expires_at,
                now=now,
                scope=scope,
            ),
            message="failed to persist Worker credential",
        )

    async def rotate_worker_credential(
        self,
        worker_id: str,
        credential_id: str,
        *,
        purpose: str | None = None,
        expires_at: datetime | None = None,
        scope: CredentialScope | None = None,
        now: datetime | None = None,
    ) -> CredentialRotation:
        return await self._run(
            lambda: self._worker_service.rotate_worker_credential(
                worker_id,
                credential_id,
                purpose=purpose,
                expires_at=expires_at,
                scope=scope,
                now=now,
            ),
            message="failed to persist Worker credential rotation",
        )


def runtime_worker_credential_service(
    service: LocalAuthenticationService,
    *,
    runtime_service: AsyncWorkerCredentialService | None = None,
) -> AsyncWorkerCredentialService:
    if runtime_service is not None:
        return runtime_service
    return AsyncWorkerCredentialServiceAdapter(service)


__all__ = [
    "AsyncWorkerCredentialService",
    "AsyncWorkerCredentialServiceAdapter",
    "runtime_worker_credential_service",
]
