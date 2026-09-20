from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from ai_multi_agent_platform.security.authentication import (
    AuthenticationError,
    AuthenticationFailure,
    AuthenticationMethod,
)
from ai_multi_agent_platform.security.authentication_hardening import LocalAuthenticationService

NOW = datetime(2026, 9, 20, 20, 0, tzinfo=UTC)
PASSWORD = "correct horse battery staple 1320!"


def _service(*, audit=None) -> LocalAuthenticationService:
    return LocalAuthenticationService(audit_sink=audit.append if audit is not None else None)


def test_mobile_pairing_is_single_use_and_issues_revocable_device_credential() -> None:
    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    grant = auth.mobile_pairing.create_challenge(
        user.user_id,
        "https://platform.example",
        now=NOW,
        correlation_id="corr-create",
    )

    device, issued = auth.mobile_pairing.consume_challenge(
        grant.pairing_id,
        grant.secret,
        device_name="Alice phone",
        platform="android",
        now=NOW + timedelta(seconds=1),
        correlation_id="corr-consume",
    )

    actor = auth.authenticate_bearer(issued.secret, now=NOW + timedelta(seconds=2))
    assert actor.identity.actor_id == user.user_id
    assert actor.method is AuthenticationMethod.MOBILE_DEVICE_TOKEN
    listed = auth.mobile_pairing.list_devices(user.user_id)
    assert listed == (device,)
    safe = auth.mobile_pairing.safe_device(device, now=NOW + timedelta(seconds=2))
    assert safe["display_name"] == "Alice phone"
    assert safe["active"] is True
    assert "secret" not in safe
    assert grant.secret not in repr(safe)

    with pytest.raises(AuthenticationError) as replay:
        auth.mobile_pairing.consume_challenge(
            grant.pairing_id,
            grant.secret,
            device_name="Replay",
            now=NOW + timedelta(seconds=3),
        )
    assert replay.value.failure is AuthenticationFailure.REPLAY_REJECTED

    auth.mobile_pairing.revoke_device(
        user.user_id,
        device.device_id,
        now=NOW + timedelta(seconds=4),
    )
    with pytest.raises(AuthenticationError) as revoked:
        auth.authenticate_bearer(issued.secret, now=NOW + timedelta(seconds=5))
    assert revoked.value.failure is AuthenticationFailure.CREDENTIAL_REVOKED


def test_mobile_pairing_fallback_code_resolves_without_pairing_id() -> None:
    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    grant = auth.mobile_pairing.create_challenge(
        user.user_id,
        "https://platform.example",
        now=NOW,
    )

    device, issued = auth.mobile_pairing.consume_challenge(
        None,
        grant.secret.lower().replace("-", " "),
        device_name="Fallback phone",
        now=NOW + timedelta(seconds=1),
    )

    assert device.user_id == user.user_id
    assert auth.authenticate_bearer(issued.secret, now=NOW + timedelta(seconds=2)).identity.actor_id == user.user_id


def test_mobile_pairing_rejects_expired_cancelled_wrong_and_cross_user_use() -> None:
    auth = _service()
    alice = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    bob = auth.create_local_user("bob", PASSWORD, now=NOW)

    expired = auth.mobile_pairing.create_challenge(
        alice.user_id,
        "https://platform.example",
        now=NOW,
    )
    with pytest.raises(AuthenticationError) as expiry:
        auth.mobile_pairing.consume_challenge(
            expired.pairing_id,
            expired.secret,
            device_name="Late phone",
            now=expired.expires_at,
        )
    assert expiry.value.failure is AuthenticationFailure.CREDENTIAL_EXPIRED

    cancelled = auth.mobile_pairing.create_challenge(
        alice.user_id,
        "https://platform.example",
        now=NOW,
    )
    with pytest.raises(KeyError):
        auth.mobile_pairing.cancel_challenge(
            bob.user_id,
            cancelled.pairing_id,
            now=NOW + timedelta(seconds=1),
        )
    auth.mobile_pairing.cancel_challenge(
        alice.user_id,
        cancelled.pairing_id,
        now=NOW + timedelta(seconds=1),
    )
    with pytest.raises(ValueError, match="cancelled"):
        auth.mobile_pairing.consume_challenge(
            cancelled.pairing_id,
            cancelled.secret,
            device_name="Cancelled phone",
            now=NOW + timedelta(seconds=2),
        )

    wrong = auth.mobile_pairing.create_challenge(
        alice.user_id,
        "https://platform.example",
        now=NOW,
    )
    for attempt in range(4):
        with pytest.raises(AuthenticationError) as invalid:
            auth.mobile_pairing.consume_challenge(
                wrong.pairing_id,
                f"WRONG-{attempt}",
                device_name="Attacker",
                now=NOW + timedelta(seconds=attempt + 1),
            )
        assert invalid.value.failure is AuthenticationFailure.INVALID_CREDENTIALS
    with pytest.raises(AuthenticationError) as limited:
        auth.mobile_pairing.consume_challenge(
            wrong.pairing_id,
            "WRONG-LAST",
            device_name="Attacker",
            now=NOW + timedelta(seconds=5),
        )
    assert limited.value.failure is AuthenticationFailure.RATE_LIMITED


def test_mobile_pairing_requires_tls_for_remote_origin() -> None:
    auth = _service()
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)

    with pytest.raises(ValueError, match="requires HTTPS"):
        auth.mobile_pairing.create_challenge(
            user.user_id,
            "http://platform.example",
            now=NOW,
        )

    local = auth.mobile_pairing.create_challenge(
        user.user_id,
        "http://127.0.0.1:8000",
        now=NOW,
    )
    assert local.server_origin == "http://127.0.0.1:8000"


def test_mobile_pairing_audit_records_are_redacted() -> None:
    audit = []
    auth = _service(audit=audit)
    user = auth.bootstrap_first_admin("alice", PASSWORD, now=NOW)
    grant = auth.mobile_pairing.create_challenge(
        user.user_id,
        "https://platform.example",
        now=NOW,
        correlation_id="corr-create",
    )
    device, issued = auth.mobile_pairing.consume_challenge(
        grant.pairing_id,
        grant.secret,
        device_name="Alice phone",
        now=NOW + timedelta(seconds=1),
        correlation_id="corr-consume",
    )
    auth.mobile_pairing.revoke_device(
        user.user_id,
        device.device_id,
        now=NOW + timedelta(seconds=2),
        correlation_id="corr-revoke",
    )

    rendered = repr(audit)
    assert grant.secret not in rendered
    assert issued.secret not in rendered
    assert any(item.event == "auth.mobile_pairing_created" for item in audit)
    assert any(item.event == "auth.mobile_pairing_consumed" and item.success for item in audit)
    assert any(item.event == "auth.mobile_device_revoked" for item in audit)
