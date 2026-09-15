from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ai_multi_agent_platform.security import LocalAuthenticationService, ScryptPasswordHasher
from ai_multi_agent_platform.security.async_worker_credentials import (
    AsyncWorkerCredentialServiceAdapter,
)

_NOW = datetime(2026, 9, 15, 12, 0, tzinfo=UTC)


def _service() -> LocalAuthenticationService:
    return LocalAuthenticationService(
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024)
    )


def test_concurrent_worker_credential_provisioning_is_idempotent() -> None:
    service = _service()
    first = AsyncWorkerCredentialServiceAdapter(service)
    second = AsyncWorkerCredentialServiceAdapter(service)
    worker_id = "worker:credential-provisioning"

    async def scenario() -> None:
        results = await asyncio.gather(
            first.provision_worker_credential(worker_id, now=_NOW),
            second.provision_worker_credential(worker_id, now=_NOW),
        )

        issued = tuple(result.issued for result in results if result.issued is not None)
        assert len(issued) == 1
        active = tuple(
            credential
            for credential in service.list_credentials(worker_id)
            if credential.active(now=_NOW)
        )
        assert len(active) == 1
        assert active[0].credential_id == issued[0].credential_id
        reused = next(result for result in results if result.issued is None)
        assert reused.active_credential_ids == (active[0].credential_id,)

    asyncio.run(scenario())
