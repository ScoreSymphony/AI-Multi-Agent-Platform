from __future__ import annotations

import pytest

from ai_multi_agent_platform.deployment import build_single_node_deployment
from ai_multi_agent_platform.deployment.config import SingleNodeConfig
from ai_multi_agent_platform.security import AuthenticationError


OLD_PASSWORD = "old-password-with-sufficient-length"
NEW_PASSWORD = "new-password-with-sufficient-length"


def test_reset_admin_password_recovers_sole_local_administrator(tmp_path) -> None:
    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "single-node", secure_cookie=False)
    )
    admin = deployment.bootstrap_admin("admin", OLD_PASSWORD)
    session = deployment.authentication.create_browser_session(admin.user_id)

    recovered = deployment.reset_admin_password("admin", NEW_PASSWORD)

    assert recovered.user_id == admin.user_id
    deployment.authentication.authenticate_password("admin", NEW_PASSWORD)
    with pytest.raises(AuthenticationError):
        deployment.authentication.authenticate_password("admin", OLD_PASSWORD)
    with pytest.raises(AuthenticationError):
        deployment.authentication.authenticate_session(session.token)


def test_reset_admin_password_requires_existing_admin_policy(tmp_path) -> None:
    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "single-node", secure_cookie=False)
    )
    account = deployment.authentication.bootstrap_first_admin("admin", OLD_PASSWORD)

    with pytest.raises(ValueError, match="existing administrator policy"):
        deployment.reset_admin_password("admin", NEW_PASSWORD)

    deployment.authentication.authenticate_password(account.username, OLD_PASSWORD)
