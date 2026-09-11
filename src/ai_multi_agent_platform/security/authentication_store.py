"""Authentication identity/session/credential reference store."""

from __future__ import annotations

from .authentication_models import (
    BrowserSession,
    ExternalIdentityMapping,
    LocalUserAccount,
    StoredCredential,
)


class InMemoryAuthenticationStore:
    """Deterministic self-hosted/reference store with no retrievable secret material."""

    def __init__(self) -> None:
        self.users: dict[str, LocalUserAccount] = {}
        self.usernames: dict[str, str] = {}
        self.sessions: dict[str, BrowserSession] = {}
        self.credentials: dict[str, StoredCredential] = {}
        self.external_mappings: dict[tuple[str, str, str], ExternalIdentityMapping] = {}

    def add_user(self, account: LocalUserAccount) -> None:
        normalized = normalize_username(account.username)
        if account.user_id in self.users or normalized in self.usernames:
            raise ValueError("local user already exists")
        self.users[account.user_id] = account
        self.usernames[normalized] = account.user_id

    def update_user(self, account: LocalUserAccount) -> None:
        if account.user_id not in self.users:
            raise KeyError(account.user_id)
        self.users[account.user_id] = account

    def user_by_username(self, username: str) -> LocalUserAccount | None:
        user_id = self.usernames.get(normalize_username(username))
        return self.users.get(user_id) if user_id is not None else None


def normalize_username(username: str) -> str:
    return username.strip().casefold()


__all__ = ["InMemoryAuthenticationStore"]
