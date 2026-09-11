"""Safe northbound serialization for authentication records."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue

from .authentication_models import AuthenticatedActor, BrowserSession, StoredCredential
from .authentication_tokens import authentication_now


def safe_session(session: BrowserSession, *, now: object | None = None) -> dict[str, JsonValue]:
    # Keep the historical callable surface while centralizing serialization ownership.
    current = authentication_now(now if hasattr(now, "tzinfo") else None)
    return {
        "id": session.session_id,
        "user_id": session.user_id,
        "created_at": session.created_at.isoformat(),
        "authenticated_at": session.authenticated_at.isoformat(),
        "expires_at": session.expires_at.isoformat(),
        "revoked_at": session.revoked_at.isoformat() if session.revoked_at else None,
        "last_seen_at": session.last_seen_at.isoformat() if session.last_seen_at else None,
        "active": session.active(now=current),
    }


def safe_credential(credential: StoredCredential) -> dict[str, JsonValue]:
    return {
        "id": credential.credential_id,
        "owner_id": credential.owner_id,
        "actor_type": credential.actor_type.value,
        "kind": credential.kind.value,
        "purpose": credential.purpose,
        "created_at": credential.created_at.isoformat(),
        "expires_at": credential.expires_at.isoformat() if credential.expires_at else None,
        "revoked_at": credential.revoked_at.isoformat() if credential.revoked_at else None,
        "last_used_at": credential.last_used_at.isoformat() if credential.last_used_at else None,
    }


def safe_actor(actor: AuthenticatedActor) -> dict[str, JsonValue]:
    metadata: dict[str, JsonValue] = {
        namespace: value for namespace, value in actor.provider_metadata.items()
    }
    return {
        "actor_id": actor.identity.actor_id,
        "actor_type": actor.identity.actor_type.value,
        "authentication_method": actor.method.value,
        "credential_id": actor.credential_id,
        "authenticated_at": actor.authenticated_at.isoformat(),
        "expires_at": actor.expires_at.isoformat() if actor.expires_at else None,
        "organization_id": actor.organization_id,
        "project_id": actor.project_id,
        "request_id": actor.request_id,
        "correlation_id": actor.correlation_id,
        "provider_metadata": metadata,
    }


__all__ = ["safe_actor", "safe_credential", "safe_session"]
