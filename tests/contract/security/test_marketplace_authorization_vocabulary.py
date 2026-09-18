from __future__ import annotations

from ai_multi_agent_platform.security import (
    AuthorizationAction,
    ResourceType,
    canonical_control_plane_vocabulary,
)


def test_marketplace_vocabulary_preserves_lifecycle_authorization_semantics() -> None:
    expected = {
        "marketplace.preview": AuthorizationAction.READ,
        "marketplace.install": AuthorizationAction.CREATE,
        "marketplace.update": AuthorizationAction.MODIFY,
        "marketplace.uninstall": AuthorizationAction.DELETE,
    }

    for command, action in expected.items():
        assert canonical_control_plane_vocabulary(command) == (
            action,
            ResourceType.GENERIC,
        )
