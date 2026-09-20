from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.security import (
    AuthenticationAuditRecord,
    AuthenticationError,
    AuthenticationFailure,
    AuthenticationMethod,
    LocalAuthenticationService,
    ScryptPasswordHasher,
    safe_mobile_device,
)
from ai_multi_agent_platform.security.sqlite_authentication import SqliteAuthenticationStore

NOW = datetime(2026, 9, 20, 19, 30, tzinfo=UTC)
PASSWORD = "correct horse battery staple"


def _service(*, store=None, audit=None) -> LocalAuthenticationService:
    return LocalAuthenticationService(
        store=store,
        password_hasher=ScryptPasswordHasher(n=2**10, r=8, p=1, maxmem=8 * 1024 * 1024),
        audit_sink=audit.append if audit is not None else None,
    )


def test_mobile_pairing_issues_scoped_revocable_credential_without_storing_pairing_secret() -> None:
    audit: list[AuthenticationAuditRecord] = []
    auth = _service(audit=audit)
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    pairing = auth.create_mobile_pairing(
        user.user_id,
        "https://platform.example",
        now=NOW,
        correlation_id="corr-pair",
    )

    stored = auth.store.mobile_pairings[pairing.pairing_id]
    assert pairing.pairing_code not in stored.secret_verifier
    assert pairing.qr_payload.startswith("aiagentplatform://pair?")
    assert "amp1." not in pairing.qr_payload

    grant = auth.consume_mobile_pairing(
        pairing.pairing_code,
        pairing_id=pairing.pairing_id,
        server_origin="https://platform.example",
        device_name="Pixel 10",
        device_platform="android",
        now=NOW + timedelta(seconds=1),
        correlation_id="corr-consume",
    )
    actor = auth.authenticate_bearer(grant.credential.secret, now=NOW + timedelta(seconds=2))
    assert actor.identity.actor_id == user.user_id
    assert actor.method is AuthenticationMethod.MOBILE_TOKEN

    scope = auth.credential_scope(grant.credential.credential_id)
    assert scope.restricted is True
    assert "manage_credentials" not in {action.value for action in scope.actions}

    safe = safe_mobile_device(
        grant.device,
        auth.store.credentials[grant.device.credential_id],
        now=NOW + timedelta(seconds=2),
    )
    assert safe["active"] is True
    assert "secret" not in repr(safe).casefold()
    assert pairing.pairing_code not in repr(audit)
    assert grant.credential.secret not in repr(audit)


def test_mobile_pairing_fallback_code_replay_expiry_cancel_and_failed_attempt_limit() -> None:
    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)

    fallback = auth.create_mobile_pairing(user.user_id, "https://platform.example", now=NOW)
    auth.consume_mobile_pairing(
        fallback.pairing_code,
        server_origin="https://platform.example",
        device_name="Fallback",
        device_platform="android",
        now=NOW + timedelta(seconds=1),
    )
    with pytest.raises(AuthenticationError) as replay:
        auth.consume_mobile_pairing(
            fallback.pairing_code,
            server_origin="https://platform.example",
            device_name="Replay",
            device_platform="android",
            now=NOW + timedelta(seconds=2),
        )
    assert replay.value.failure is AuthenticationFailure.REPLAY_REJECTED

    expired = auth.create_mobile_pairing(user.user_id, "https://platform.example", now=NOW)
    with pytest.raises(AuthenticationError) as expired_error:
        auth.consume_mobile_pairing(
            expired.pairing_code,
            server_origin="https://platform.example",
            device_name="Expired",
            device_platform="android",
            now=NOW + timedelta(minutes=5),
        )
    assert expired_error.value.failure is AuthenticationFailure.CREDENTIAL_EXPIRED

    cancelled = auth.create_mobile_pairing(user.user_id, "https://platform.example", now=NOW)
    auth.cancel_mobile_pairing(user.user_id, cancelled.pairing_id, now=NOW + timedelta(seconds=1))
    with pytest.raises(AuthenticationError) as cancelled_error:
        auth.consume_mobile_pairing(
            cancelled.pairing_code,
            server_origin="https://platform.example",
            device_name="Cancelled",
            device_platform="android",
            now=NOW + timedelta(seconds=2),
        )
    assert cancelled_error.value.failure is AuthenticationFailure.CREDENTIAL_REVOKED

    limited = auth.create_mobile_pairing(user.user_id, "https://platform.example", now=NOW)
    locator = limited.pairing_code.split("-", 1)[0]
    wrong = f"{locator}-AAAAAAAAAAAAAAAAAAAA"
    for attempt in range(4):
        with pytest.raises(AuthenticationError) as invalid:
            auth.consume_mobile_pairing(
                wrong,
                pairing_id=limited.pairing_id,
                server_origin="https://platform.example",
                device_name="Wrong",
                device_platform="android",
                now=NOW + timedelta(seconds=attempt + 1),
            )
        assert invalid.value.failure is AuthenticationFailure.INVALID_CREDENTIALS
    with pytest.raises(AuthenticationError) as rate_limited:
        auth.consume_mobile_pairing(
            wrong,
            pairing_id=limited.pairing_id,
            server_origin="https://platform.example",
            device_name="Wrong",
            device_platform="android",
            now=NOW + timedelta(seconds=5),
        )
    assert rate_limited.value.failure is AuthenticationFailure.RATE_LIMITED


def test_mobile_pairing_rejects_remote_http_and_cross_user_management() -> None:
    auth = _service()
    alice = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    bob = auth.create_local_user("bob", PASSWORD, now=NOW)

    with pytest.raises(ValueError, match="requires HTTPS"):
        auth.create_mobile_pairing(alice.user_id, "http://platform.example", now=NOW)

    pairing = auth.create_mobile_pairing(alice.user_id, "https://platform.example", now=NOW)
    with pytest.raises(KeyError):
        auth.cancel_mobile_pairing(bob.user_id, pairing.pairing_id, now=NOW)

    grant = auth.consume_mobile_pairing(
        pairing.pairing_code,
        server_origin="https://platform.example",
        device_name="Alice phone",
        device_platform="android",
        now=NOW + timedelta(seconds=1),
    )
    with pytest.raises(KeyError):
        auth.revoke_mobile_device(bob.user_id, grant.device.device_id, now=NOW)


def test_mobile_device_revocation_rejects_next_canonical_bearer_request() -> None:
    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    pairing = auth.create_mobile_pairing(user.user_id, "https://platform.example", now=NOW)
    grant = auth.consume_mobile_pairing(
        pairing.pairing_code,
        server_origin="https://platform.example",
        device_name="Pixel",
        device_platform="android",
        now=NOW + timedelta(seconds=1),
    )

    auth.authenticate_bearer(grant.credential.secret, now=NOW + timedelta(seconds=2))
    auth.revoke_mobile_device(user.user_id, grant.device.device_id, now=NOW + timedelta(seconds=3))
    with pytest.raises(AuthenticationError) as revoked:
        auth.authenticate_bearer(grant.credential.secret, now=NOW + timedelta(seconds=4))
    assert revoked.value.failure is AuthenticationFailure.CREDENTIAL_REVOKED


def test_mobile_pairing_and_revocation_survive_sqlite_restart(tmp_path) -> None:
    path = tmp_path / "authentication.sqlite3"
    first = _service(store=SqliteAuthenticationStore(path))
    user = first.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    pairing = first.create_mobile_pairing(user.user_id, "https://platform.example", now=NOW)

    restarted = _service(store=SqliteAuthenticationStore(path))
    grant = restarted.consume_mobile_pairing(
        pairing.pairing_code,
        pairing_id=pairing.pairing_id,
        server_origin="https://platform.example",
        device_name="Restarted phone",
        device_platform="android",
        now=NOW + timedelta(seconds=1),
    )
    restarted.revoke_mobile_device(
        user.user_id,
        grant.device.device_id,
        now=NOW + timedelta(seconds=2),
    )

    final = _service(store=SqliteAuthenticationStore(path))
    devices = final.list_mobile_devices(user.user_id)
    assert [item.device_id for item in devices] == [grant.device.device_id]
    credential = final.store.credentials[grant.device.credential_id]
    assert credential.revoked_at == NOW + timedelta(seconds=2)
    with pytest.raises(AuthenticationError) as replay:
        final.consume_mobile_pairing(
            pairing.pairing_code,
            pairing_id=pairing.pairing_id,
            server_origin="https://platform.example",
            device_name="Replay after restart",
            device_platform="android",
            now=NOW + timedelta(seconds=3),
        )
    assert replay.value.failure is AuthenticationFailure.REPLAY_REJECTED
