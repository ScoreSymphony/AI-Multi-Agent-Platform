from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.security import (
    AuthenticationError,
    AuthenticationFailure,
    LocalAuthenticationService,
    ScryptPasswordHasher,
)
from ai_multi_agent_platform.security.sqlite_authentication import SqliteAuthenticationStore

NOW = datetime(2026, 9, 20, 19, 0, tzinfo=UTC)
PASSWORD = "correct horse battery staple"


def _service(path: Path) -> LocalAuthenticationService:
    return LocalAuthenticationService(
        store=SqliteAuthenticationStore(path),
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024),
    )


def test_mobile_device_credential_metadata_and_revocation_survive_sqlite_restart(
    tmp_path: Path,
) -> None:
    path = tmp_path / "authentication.sqlite3"
    first = _service(path)
    user = first.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    challenge = first.create_mobile_pairing_challenge(
        user.user_id,
        server_origin="https://platform.example",
        now=NOW,
    )
    issued = first.complete_mobile_pairing(
        challenge.request_id,
        challenge.secret,
        device_name="Restart device",
        device_metadata={"platform": "android", "app_version": "0.1.0"},
        now=NOW + timedelta(seconds=1),
    )

    restarted = _service(path)
    devices = restarted.list_mobile_devices(user.user_id)
    assert len(devices) == 1
    assert devices[0].credential_id == issued.credential_id
    assert devices[0].metadata["client"] == "mobile"
    assert devices[0].metadata["device_name"] == "Restart device"
    assert (
        restarted.authenticate_bearer(
            issued.secret,
            now=NOW + timedelta(seconds=2),
        ).identity.actor_id
        == user.user_id
    )

    restarted.revoke_mobile_device(
        user.user_id,
        issued.credential_id,
        now=NOW + timedelta(seconds=3),
    )
    after_revocation_restart = _service(path)
    with pytest.raises(AuthenticationError) as exc:
        after_revocation_restart.authenticate_bearer(
            issued.secret,
            now=NOW + timedelta(seconds=4),
        )
    assert exc.value.failure is AuthenticationFailure.CREDENTIAL_REVOKED
