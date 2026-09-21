from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from ai_multi_agent_platform.security.authentication_hardening import LocalAuthenticationService
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

    second = _service(database)
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
