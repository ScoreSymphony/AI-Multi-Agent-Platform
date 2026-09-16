"""Canonical first-user bootstrap composition for browser and operator first run.

Authentication establishes the local human identity and browser session. Authorization remains
explicit: the initial administrator policy is installed through the existing local #15 policy
store rather than inferred from successful authentication.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum

from .async_authentication import (
    AsyncAuthenticationService,
    runtime_authentication_service,
)
from .async_authorization_policy import (
    AsyncAuthorizationPolicyService,
    LocalAuthorizationPolicyStore,
    runtime_authorization_policy_service,
)
from .authentication import LoginResult
from .authentication_hardening import LocalAuthenticationService
from .authorization import ActorType, LocalPrincipalPolicy

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_BYTES = 1024


class FirstUserBootstrapState(StrEnum):
    """Public, non-sensitive initialization state for local first-user bootstrap."""

    UNINITIALIZED = "uninitialized"
    INCOMPLETE = "incomplete"
    INITIALIZED = "initialized"


@dataclass(frozen=True, slots=True)
class FirstUserBootstrapStatus:
    """Minimal public bootstrap state; never exposes account identities or secret material."""

    state: FirstUserBootstrapState
    bootstrap_available: bool
    password_min_length: int = PASSWORD_MIN_LENGTH
    password_max_bytes: int = PASSWORD_MAX_BYTES


@dataclass(frozen=True, slots=True)
class FirstUserBootstrapResult:
    """Completed first-user bootstrap with explicit authorization and browser session."""

    login: LoginResult
    authorization_granted: bool


class FirstUserBootstrapUnavailable(ValueError):
    """Raised when the public first-user bootstrap is already closed."""


class FirstUserBootstrapService:
    """Compose canonical authentication and authorization into one first-user operation.

    The service deliberately owns no second account, session, or policy store. It coordinates the
    existing durable authorities and permits recovery only from the narrow partial state where
    exactly one local account exists but its initial administrator policy was not persisted yet.
    """

    def __init__(
        self,
        authentication: LocalAuthenticationService,
        authorization: LocalAuthorizationPolicyStore,
        *,
        runtime_authentication: AsyncAuthenticationService | None = None,
        runtime_authorization_policies: AsyncAuthorizationPolicyService | None = None,
    ) -> None:
        self.authentication = authentication
        self.authorization = authorization
        self.runtime_authentication = runtime_authentication_service(
            authentication,
            runtime_service=runtime_authentication,
        )
        self.runtime_authorization_policies = runtime_authorization_policy_service(
            authorization,
            runtime_service=runtime_authorization_policies,
        )
        self._bootstrap_lock = asyncio.Lock()

    async def status(self) -> FirstUserBootstrapStatus:
        async with self._bootstrap_lock:
            return await self._status_unlocked()

    async def _status_unlocked(self) -> FirstUserBootstrapStatus:
        accounts = tuple(self.authentication.store.users.values())
        if not accounts:
            state = FirstUserBootstrapState.UNINITIALIZED
        elif len(accounts) == 1 and not await self.runtime_authorization_policies.has_policy(
            accounts[0].user_id
        ):
            state = FirstUserBootstrapState.INCOMPLETE
        else:
            state = FirstUserBootstrapState.INITIALIZED
        return FirstUserBootstrapStatus(
            state=state,
            bootstrap_available=state is not FirstUserBootstrapState.INITIALIZED,
        )

    async def bootstrap(
        self,
        username: str,
        password: str,
        *,
        request_id: str | None = None,
        correlation_id: str | None = None,
    ) -> FirstUserBootstrapResult:
        """Create/recover exactly one first user, install admin policy, and establish a session."""

        async with self._bootstrap_lock:
            accounts = tuple(self.authentication.store.users.values())
            login: LoginResult | None = None

            if not accounts:
                account = await self.runtime_authentication.bootstrap_first_admin(
                    username,
                    password,
                    correlation_id=correlation_id,
                )
            elif len(accounts) == 1:
                account = accounts[0]
                if await self.runtime_authorization_policies.has_policy(account.user_id):
                    raise FirstUserBootstrapUnavailable(
                        "first-user bootstrap is already complete"
                    )
                # A prior attempt may have persisted the identity but failed before policy/session
                # completion. Recovery is allowed only by proving the existing account password.
                login = await self.runtime_authentication.login(
                    username,
                    password,
                    request_id=request_id,
                    correlation_id=correlation_id,
                )
                if login.actor.identity.actor_id != account.user_id:
                    raise FirstUserBootstrapUnavailable(
                        "first-user bootstrap cannot target another local account"
                    )
            else:
                raise FirstUserBootstrapUnavailable(
                    "first-user bootstrap is unavailable after local account initialization"
                )

            await self.runtime_authorization_policies.ensure_registered(
                initial_administrator_policy(account.user_id)
            )

            if login is None:
                login = await self.runtime_authentication.login(
                    username,
                    password,
                    request_id=request_id,
                    correlation_id=correlation_id,
                )

            return FirstUserBootstrapResult(
                login=login,
                authorization_granted=True,
            )


def initial_administrator_policy(user_id: str) -> LocalPrincipalPolicy:
    """Return the explicit V1 local administrator policy used by all bootstrap surfaces."""

    return LocalPrincipalPolicy(
        principal_ref=user_id,
        actor_types=frozenset({ActorType.HUMAN}),
        administrator=True,
    )


__all__ = [
    "FirstUserBootstrapResult",
    "FirstUserBootstrapService",
    "FirstUserBootstrapState",
    "FirstUserBootstrapStatus",
    "FirstUserBootstrapUnavailable",
    "PASSWORD_MAX_BYTES",
    "PASSWORD_MIN_LENGTH",
    "initial_administrator_policy",
]
