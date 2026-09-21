from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier

import pytest

from ai_multi_agent_platform.security.authentication_hardening import LocalAuthenticationService
from ai_multi_agent_platform.security.authentication_models import (
    AuthenticationAuditRecord,
    AuthenticationError,
    AuthenticationFailure,
    CredentialKind,
    PairedMobileDevice,
    StoredCredential,
)
from ai_multi_agent_platform.security.sqlite_authentication import SqliteAuthenticationStore

NOW = datetime(2026, 9, 20, 20, 0, tzinfo=UTC)
PASSWORD = "correct horse battery staple 1320!"


def _service(path: Path) -> LocalAuthenticationService:
    return LocalAuthenticationService(store=SqliteAuthenticationStore(path))


def test_mobile_pairing_and_device_credential_survive_restart_without_raw_secrets(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authentication.sqlite3"
    first = _service(database)
    user = first.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    grant = first.mobile_pairing.create_challenge(
        user.user_id,
        "https://platform.example",
        now=NOW,
    )

    audit: list[AuthenticationAuditRecord] = []
    second = LocalAuthenticationService(
        store=SqliteAuthenticationStore(database),
        audit_sink=audit.append,
    )
    device, issued = second.mobile_pairing.consume_challenge(
        grant.pairing_id,
        grant.secret,
        device_name="Alice Android",
        platform="android",
        now=NOW + timedelta(seconds=1),
    )

    third = _service(database)
    restored = third.mobile_pairing.list_devices(user.user_id)
    assert len(restored) == 1
    assert restored[0].device_id == device.device_id
    assert (
        third.authenticate_bearer(
            issued.secret,
            now=NOW + timedelta(seconds=2),
        ).identity.actor_id
        == user.user_id
    )

    with sqlite3.connect(database) as connection:
        pairing_verifier = connection.execute(
            "SELECT secret_verifier FROM auth_mobile_pairings WHERE pairing_id = ?",
            (grant.pairing_id,),
        ).fetchone()
        credential_verifier = connection.execute(
            "SELECT secret_verifier FROM auth_credentials WHERE credential_id = ?",
            (issued.credential_id,),
        ).fetchone()
    assert pairing_verifier is not None
    assert credential_verifier is not None
    assert grant.secret not in str(pairing_verifier[0])
    assert issued.secret not in str(credential_verifier[0])
    created = [item for item in audit if item.event == "auth.credential_created"]
    assert len(created) == 1
    assert created[0].credential_id == issued.credential_id


def test_expired_mobile_pairings_are_pruned_from_sqlite_and_restart_state(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authentication.sqlite3"
    first = _service(database)
    user = first.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    old = first.mobile_pairing.create_challenge(
        user.user_id,
        "https://platform.example",
        now=NOW,
    )
    first.mobile_pairing.consume_challenge(
        old.pairing_id,
        old.secret,
        device_name="Old Android",
        now=NOW + timedelta(seconds=1),
    )

    fresh = first.mobile_pairing.create_challenge(
        user.user_id,
        "https://platform.example",
        now=NOW + timedelta(minutes=6),
    )

    with sqlite3.connect(database) as connection:
        rows = connection.execute(
            "SELECT pairing_id FROM auth_mobile_pairings ORDER BY pairing_id"
        ).fetchall()
    persisted_ids = {str(row[0]) for row in rows}
    assert old.pairing_id not in persisted_ids
    assert fresh.pairing_id in persisted_ids

    restarted = _service(database)
    assert old.pairing_id not in restarted.store.mobile_pairings
    assert fresh.pairing_id in restarted.store.mobile_pairings


class _FaultInjectingSqliteAuthenticationStore(SqliteAuthenticationStore):
    def __init__(self, path: Path) -> None:
        self.fail_mobile_credential = False
        self.fail_mobile_device = False
        super().__init__(path)

    def _persist_credential(self, key: str, credential: StoredCredential) -> None:
        if self.fail_mobile_credential and credential.kind is CredentialKind.MOBILE:
            raise sqlite3.OperationalError("injected mobile credential persistence failure")
        super()._persist_credential(key, credential)

    def _persist_mobile_device(self, key: str, device: PairedMobileDevice) -> None:
        if self.fail_mobile_device:
            raise sqlite3.OperationalError("injected mobile device persistence failure")
        super()._persist_mobile_device(key, device)


def _assert_failed_pairing_is_retryable(
    database: Path,
    *,
    user_id: str,
    pairing_id: str,
    pairing_secret: str,
) -> None:
    restarted = _service(database)
    challenge = restarted.store.mobile_pairings[pairing_id]
    assert challenge.consumed_at is None
    assert restarted.mobile_pairing.list_devices(user_id) == ()
    assert not [
        credential
        for credential in restarted.store.credentials.values()
        if credential.owner_id == user_id and credential.kind is CredentialKind.MOBILE
    ]

    with sqlite3.connect(database) as connection:
        consumed_at = connection.execute(
            "SELECT consumed_at FROM auth_mobile_pairings WHERE pairing_id = ?",
            (pairing_id,),
        ).fetchone()
        mobile_credentials = connection.execute(
            "SELECT COUNT(*) FROM auth_credentials WHERE owner_id = ? AND kind = ?",
            (user_id, CredentialKind.MOBILE.value),
        ).fetchone()
        mobile_devices = connection.execute(
            "SELECT COUNT(*) FROM auth_mobile_devices WHERE user_id = ?",
            (user_id,),
        ).fetchone()
    assert consumed_at == (None,)
    assert mobile_credentials == (0,)
    assert mobile_devices == (0,)

    device, issued = restarted.mobile_pairing.consume_challenge(
        pairing_id,
        pairing_secret,
        device_name="Recovered Android",
        platform="android",
        now=NOW + timedelta(seconds=3),
    )
    assert device.credential_id == issued.credential_id


def test_mobile_pairing_rolls_back_when_mobile_credential_persistence_fails(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authentication.sqlite3"
    first = _service(database)
    user = first.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    grant = first.mobile_pairing.create_challenge(
        user.user_id,
        "https://platform.example",
        now=NOW,
    )

    store = _FaultInjectingSqliteAuthenticationStore(database)
    store.fail_mobile_credential = True
    audit: list[AuthenticationAuditRecord] = []
    failing = LocalAuthenticationService(store=store, audit_sink=audit.append)

    with pytest.raises(sqlite3.OperationalError, match="mobile credential persistence"):
        failing.mobile_pairing.consume_challenge(
            grant.pairing_id,
            grant.secret,
            device_name="Alice Android",
            platform="android",
            now=NOW + timedelta(seconds=1),
        )

    assert failing.store.mobile_pairings[grant.pairing_id].consumed_at is None
    assert failing.mobile_pairing.list_devices(user.user_id) == ()
    assert not [item for item in audit if item.event == "auth.credential_created"]
    assert not [
        credential
        for credential in failing.store.credentials.values()
        if credential.owner_id == user.user_id and credential.kind is CredentialKind.MOBILE
    ]
    _assert_failed_pairing_is_retryable(
        database,
        user_id=user.user_id,
        pairing_id=grant.pairing_id,
        pairing_secret=grant.secret,
    )


def test_mobile_pairing_rolls_back_when_device_persistence_fails(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authentication.sqlite3"
    first = _service(database)
    user = first.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    grant = first.mobile_pairing.create_challenge(
        user.user_id,
        "https://platform.example",
        now=NOW,
    )

    store = _FaultInjectingSqliteAuthenticationStore(database)
    store.fail_mobile_device = True
    audit: list[AuthenticationAuditRecord] = []
    failing = LocalAuthenticationService(store=store, audit_sink=audit.append)

    with pytest.raises(sqlite3.OperationalError, match="mobile device persistence"):
        failing.mobile_pairing.consume_challenge(
            grant.pairing_id,
            grant.secret,
            device_name="Alice Android",
            platform="android",
            now=NOW + timedelta(seconds=1),
        )

    assert failing.store.mobile_pairings[grant.pairing_id].consumed_at is None
    assert failing.mobile_pairing.list_devices(user.user_id) == ()
    assert not [item for item in audit if item.event == "auth.credential_created"]
    assert not [
        credential
        for credential in failing.store.credentials.values()
        if credential.owner_id == user.user_id and credential.kind is CredentialKind.MOBILE
    ]
    _assert_failed_pairing_is_retryable(
        database,
        user_id=user.user_id,
        pairing_id=grant.pairing_id,
        pairing_secret=grant.secret,
    )


def test_mobile_pairing_is_single_use_across_sqlite_service_instances(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authentication.sqlite3"
    first = _service(database)
    user = first.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    grant = first.mobile_pairing.create_challenge(
        user.user_id,
        "https://platform.example",
        now=NOW,
    )

    left = _service(database)
    right = _service(database)
    barrier = Barrier(2)

    def consume(service: LocalAuthenticationService) -> tuple[str, object]:
        barrier.wait(timeout=5)
        try:
            device, _issued = service.mobile_pairing.consume_challenge(
                grant.pairing_id,
                grant.secret,
                device_name="Concurrent Android",
                platform="android",
                now=NOW + timedelta(seconds=1),
            )
        except AuthenticationError as exc:
            return ("error", exc.failure)
        return ("ok", device.device_id)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(consume, (left, right)))

    successes = [value for status, value in results if status == "ok"]
    failures = [value for status, value in results if status == "error"]
    assert len(successes) == 1
    assert failures == [AuthenticationFailure.REPLAY_REJECTED]

    restarted = _service(database)
    devices = restarted.mobile_pairing.list_devices(user.user_id)
    mobile_credentials = [
        credential
        for credential in restarted.store.credentials.values()
        if credential.owner_id == user.user_id and credential.kind is CredentialKind.MOBILE
    ]
    assert len(devices) == 1
    assert len(mobile_credentials) == 1
    assert devices[0].credential_id == mobile_credentials[0].credential_id


class _FailingCommittedPairingAudit:
    def __init__(self) -> None:
        self.events: list[str] = []

    def __call__(self, record: AuthenticationAuditRecord) -> None:
        self.events.append(record.event)
        if record.event in {"auth.credential_created", "auth.mobile_pairing_consumed"}:
            raise RuntimeError("injected post-commit audit failure")


def test_mobile_pairing_returns_committed_credential_when_post_commit_audit_fails(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authentication.sqlite3"
    audit = _FailingCommittedPairingAudit()
    service = LocalAuthenticationService(
        store=SqliteAuthenticationStore(database),
        audit_sink=audit,
    )
    user = service.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    grant = service.mobile_pairing.create_challenge(
        user.user_id,
        "https://platform.example",
        now=NOW,
    )

    device, issued = service.mobile_pairing.consume_challenge(
        grant.pairing_id,
        grant.secret,
        device_name="Audit-failure Android",
        platform="android",
        now=NOW + timedelta(seconds=1),
    )

    assert "auth.credential_created" in audit.events
    assert "auth.mobile_pairing_consumed" in audit.events

    restarted = _service(database)
    restored = restarted.mobile_pairing.list_devices(user.user_id)
    assert len(restored) == 1
    assert restored[0].device_id == device.device_id
    assert (
        restarted.authenticate_bearer(
            issued.secret,
            now=NOW + timedelta(seconds=2),
        ).identity.actor_id
        == user.user_id
    )
