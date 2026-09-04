"""Authenticated public HTTP composition for canonical conversations (issue #72)."""

from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.security.authentication_hardening import LocalAuthenticationService

from .authentication_hardening import AuthenticatedControlPlaneHTTP as _BaseAuthenticatedHTTP
from .conversation_streaming_http import ControlPlaneHTTP


class AuthenticatedControlPlaneHTTP(_BaseAuthenticatedHTTP):
    """Route authenticated requests through the current conversation-aware HTTP surface."""

    def __init__(
        self,
        control_plane: Any,
        authentication: LocalAuthenticationService,
        *,
        cookie_name: str = "amp_session",
        secure_cookie: bool = True,
    ) -> None:
        super().__init__(
            control_plane,
            authentication,
            cookie_name=cookie_name,
            secure_cookie=secure_cookie,
        )
        self._current_http = ControlPlaneHTTP(control_plane)


__all__ = ["AuthenticatedControlPlaneHTTP"]
