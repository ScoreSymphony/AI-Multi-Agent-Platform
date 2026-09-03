"""Durable SQLite authentication store for the single-node self-hosted profile."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable, Mapping
from datetime import datetime
from pathlib import Path
from typing import TypeVar

from ai_multi_agent_platform.contracts.types import JsonValue

from .authentication import (
    BrowserSession,
    CredentialKind,
    ExternalIdentityMapping,
    InMemoryAuthenticationStore,
    LocalUserAccount,
    StoredCredential,
)
from .authorization import ActorType

K = TypeVar("K")
V = TypeVar("V")


class _WriteThroughDict(dict[K, V]):
    """Small dict-compatible bridge for the existing #36 store contract."""

    def __init__(
        self,
        initial: Mapping[K, V],
        *,
        on_set: Callable[[K, V], None],
        on_delete: Callable[[K], None],
    ) -> None:
        dict.__init__(self, initial)
        self._on_set = on_set
        self._on_delete = on_delete

    def __setitem__(self, key: K, value: V) -> None:
        self._on_set(key, value)
        dict.__setitem__(self, key, value)

    def __delitem__(self, key: K) -> None:
        self._on_delete(key)
        dict.__delitem__(self, key)


class SqliteAuthenticationStore(InMemoryAuthenticationStore):
    """Persist #36 account/session/credential metadata without storing raw secrets.

    ``LocalAuthenticationService`` deliberately exposes a dict-compatible reference store.
    This implementation preserves that contract while making every mutation write-through
    to SQLite. Passwords, session secrets and bearer secrets remain represented only by the
    verifiers already defined by #36.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize_schema()
        super().__init__()

        users = self._load_users()
        sessions = self._load_sessions()
        credentials = self._load_credentials()
        external_mappings = self._load_external_mappings()

        self.users = _WriteThroughDict(
            users,
            on_set=self._persist_user,
            on_delete=lambda key: self._delete("auth_users", "user_id", key),
        )
        self.usernames = {
            account.username.strip().casefold(): account.user_id for account in users.values()
        }
        self.sessions = _WriteThroughDict(
            sessions,
            on_set=self._persist_session,
            on_delete=lambda key: self._delete("auth_sessions", "session_id", key),
        )
        self.credentials = _WriteThroughDict(
            credentials,
            on_set=self._persist_credential,
            on_delete=lambda key: self._delete("auth_credentials", "credential_id", key),
        )
        self.external_mappings = _WriteThroughDict(
            external_mappings,
            on_set=self._persist_external_mapping,
            on_delete=self._delete_external_mapping,
        )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _initialize_schema(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                PRAGMA journal_mode = WAL;
                CREATE TABLE IF NOT EXISTS auth_users (
                    user_id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_verifier TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    locked INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    password_changed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS auth_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    token_verifier TEXT NOT NULL,
                    csrf_verifier TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    authenticated_at TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked_at TEXT,
                    last_seen_at TEXT,
                    FOREIGN KEY(user_id) REFERENCES auth_users(user_id) ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS auth_credentials (
                    credential_id TEXT PRIMARY KEY,
                    owner_id TEXT NOT NULL,
                    actor_type TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    secret_verifier TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    scope_json TEXT NOT NULL,
                    expires_at TEXT,
                    revoked_at TEXT,
                    last_used_at TEXT
                );
                CREATE TABLE IF NOT EXISTS auth_external_mappings (
                    provider_id TEXT NOT NULL,
                    issuer TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    linked_at TEXT NOT NULL,
                    PRIMARY KEY(provider_id, issuer, subject),
                    FOREIGN KEY(user_id) REFERENCES auth_users(user_id) ON DELETE CASCADE
                );
                """
            )

    def _load_users(self) -> dict[str, LocalUserAccount]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT user_id, username, password_verifier, enabled, locked, created_at, "
                "password_changed_at FROM auth_users"
            ).fetchall()
        return {
            str(row[0]): LocalUserAccount(
                user_id=str(row[0]),
                username=str(row[1]),
                password_verifier=str(row[2]),
                enabled=bool(row[3]),
                locked=bool(row[4]),
                created_at=_datetime(str(row[5])),
                password_changed_at=_datetime(str(row[6])),
            )
            for row in rows
        }

    def _load_sessions(self) -> dict[str, BrowserSession]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT session_id, user_id, token_verifier, csrf_verifier, created_at, "
                "authenticated_at, expires_at, revoked_at, last_seen_at FROM auth_sessions"
            ).fetchall()
        return {
            str(row[0]): BrowserSession(
                session_id=str(row[0]),
                user_id=str(row[1]),
                token_verifier=str(row[2]),
                csrf_verifier=str(row[3]),
                created_at=_datetime(str(row[4])),
                authenticated_at=_datetime(str(row[5])),
                expires_at=_datetime(str(row[6])),
                revoked_at=_optional_datetime(row[7]),
                last_seen_at=_optional_datetime(row[8]),
            )
            for row in rows
        }

    def _load_credentials(self) -> dict[str, StoredCredential]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT credential_id, owner_id, actor_type, kind, purpose, secret_verifier, "
                "created_at, scope_json, expires_at, revoked_at, last_used_at FROM auth_credentials"
            ).fetchall()
        credentials: dict[str, StoredCredential] = {}
        for row in rows:
            scope = json.loads(str(row[7]))
            if not isinstance(scope, dict):
                raise ValueError(f"credential scope is not an object: {row[0]}")
            credentials[str(row[0])] = StoredCredential(
                credential_id=str(row[0]),
                owner_id=str(row[1]),
                actor_type=ActorType(str(row[2])),
                kind=CredentialKind(str(row[3])),
                purpose=str(row[4]),
                secret_verifier=str(row[5]),
                created_at=_datetime(str(row[6])),
                scope=_json_mapping(scope),
                expires_at=_optional_datetime(row[8]),
                revoked_at=_optional_datetime(row[9]),
                last_used_at=_optional_datetime(row[10]),
            )
        return credentials

    def _load_external_mappings(self) -> dict[tuple[str, str, str], ExternalIdentityMapping]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT provider_id, issuer, subject, user_id, linked_at "
                "FROM auth_external_mappings"
            ).fetchall()
        return {
            (str(row[0]), str(row[1]), str(row[2])): ExternalIdentityMapping(
                provider_id=str(row[0]),
                issuer=str(row[1]),
                subject=str(row[2]),
                user_id=str(row[3]),
                linked_at=_datetime(str(row[4])),
            )
            for row in rows
        }

    def _persist_user(self, _key: str, account: LocalUserAccount) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO auth_users (user_id, username, password_verifier, enabled, locked, "
                "created_at, password_changed_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(user_id) DO UPDATE SET username=excluded.username, "
                "password_verifier=excluded.password_verifier, enabled=excluded.enabled, "
                "locked=excluded.locked, password_changed_at=excluded.password_changed_at",
                (
                    account.user_id,
                    account.username,
                    account.password_verifier,
                    int(account.enabled),
                    int(account.locked),
                    account.created_at.isoformat(),
                    account.password_changed_at.isoformat(),
                ),
            )

    def _persist_session(self, _key: str, session: BrowserSession) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO auth_sessions (session_id, user_id, token_verifier, csrf_verifier, "
                "created_at, authenticated_at, expires_at, revoked_at, last_seen_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(session_id) DO UPDATE SET token_verifier=excluded.token_verifier, "
                "csrf_verifier=excluded.csrf_verifier, expires_at=excluded.expires_at, "
                "revoked_at=excluded.revoked_at, last_seen_at=excluded.last_seen_at",
                (
                    session.session_id,
                    session.user_id,
                    session.token_verifier,
                    session.csrf_verifier,
                    session.created_at.isoformat(),
                    session.authenticated_at.isoformat(),
                    session.expires_at.isoformat(),
                    _iso(session.revoked_at),
                    _iso(session.last_seen_at),
                ),
            )

    def _persist_credential(self, _key: str, credential: StoredCredential) -> None:
        scope_json = json.dumps(
            dict(credential.scope),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO auth_credentials (credential_id, owner_id, actor_type, kind, purpose, "
                "secret_verifier, created_at, scope_json, expires_at, revoked_at, last_used_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(credential_id) DO UPDATE SET owner_id=excluded.owner_id, "
                "actor_type=excluded.actor_type, kind=excluded.kind, purpose=excluded.purpose, "
                "secret_verifier=excluded.secret_verifier, scope_json=excluded.scope_json, "
                "expires_at=excluded.expires_at, revoked_at=excluded.revoked_at, "
                "last_used_at=excluded.last_used_at",
                (
                    credential.credential_id,
                    credential.owner_id,
                    credential.actor_type.value,
                    credential.kind.value,
                    credential.purpose,
                    credential.secret_verifier,
                    credential.created_at.isoformat(),
                    scope_json,
                    _iso(credential.expires_at),
                    _iso(credential.revoked_at),
                    _iso(credential.last_used_at),
                ),
            )

    def _persist_external_mapping(
        self,
        _key: tuple[str, str, str],
        mapping: ExternalIdentityMapping,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO auth_external_mappings "
                "(provider_id, issuer, subject, user_id, linked_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(provider_id, issuer, subject) DO UPDATE SET "
                "user_id=excluded.user_id, linked_at=excluded.linked_at",
                (
                    mapping.provider_id,
                    mapping.issuer,
                    mapping.subject,
                    mapping.user_id,
                    mapping.linked_at.isoformat(),
                ),
            )

    def _delete(self, table: str, key_name: str, key: object) -> None:
        allowed = {
            ("auth_users", "user_id"),
            ("auth_sessions", "session_id"),
            ("auth_credentials", "credential_id"),
        }
        if (table, key_name) not in allowed:
            raise ValueError("unsupported authentication deletion target")
        with self._connect() as connection:
            connection.execute(f"DELETE FROM {table} WHERE {key_name} = ?", (str(key),))

    def _delete_external_mapping(self, key: tuple[str, str, str]) -> None:
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM auth_external_mappings "
                "WHERE provider_id = ? AND issuer = ? AND subject = ?",
                key,
            )


def _datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("persisted authentication timestamp must be timezone-aware")
    return parsed


def _optional_datetime(value: object) -> datetime | None:
    return None if value is None else _datetime(str(value))


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _json_mapping(value: Mapping[object, object]) -> dict[str, JsonValue]:
    return {str(key): _json_value(item) for key, item in value.items()}


def _json_value(value: object) -> JsonValue:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    raise ValueError("credential scope contains unsupported JSON values")
