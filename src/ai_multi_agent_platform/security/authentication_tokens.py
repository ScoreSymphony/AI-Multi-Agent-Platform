"""Opaque authentication token parsing and credential identity helpers."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from ai_multi_agent_platform.contracts.types import JsonValue

from .authentication_models import (
    AuthenticationError,
    AuthenticationFailure,
    AuthenticationMethod,
    CredentialKind,
)
from .authorization import ActorType, infer_actor_identity


def authentication_now(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None:
        raise ValueError("authentication timestamps must be timezone-aware")
    return current


def secret_verifier(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def parse_secret(value: str, prefix: str) -> tuple[str, str]:
    try:
        actual_prefix, identifier, secret = value.split(".", 2)
    except ValueError as exc:
        raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS) from exc
    if actual_prefix != prefix or not identifier or not secret:
        raise AuthenticationError(AuthenticationFailure.INVALID_CREDENTIALS)
    return identifier, secret


def validate_actor_reference(owner_id: str, actor_type: ActorType) -> None:
    inferred = infer_actor_identity(owner_id).actor_type
    if inferred is not actor_type:
        raise ValueError(
            f"credential owner {owner_id!r} does not encode actor type {actor_type.value!r}"
        )


def validate_credential_kind(actor_type: ActorType, kind: CredentialKind) -> None:
    expected = {
        CredentialKind.PERSONAL: ActorType.HUMAN,
        CredentialKind.SERVICE: ActorType.SERVICE,
        CredentialKind.WORKER: ActorType.WORKER,
        CredentialKind.AUTOMATION: ActorType.AUTOMATION,
        CredentialKind.INTEGRATION: ActorType.INTEGRATION,
    }[kind]
    if actor_type is not expected:
        raise ValueError(f"credential kind {kind.value!r} requires actor type {expected.value!r}")


def method_for_kind(kind: CredentialKind) -> AuthenticationMethod:
    return {
        CredentialKind.PERSONAL: AuthenticationMethod.PERSONAL_ACCESS_TOKEN,
        CredentialKind.SERVICE: AuthenticationMethod.SERVICE_TOKEN,
        CredentialKind.WORKER: AuthenticationMethod.WORKER_TOKEN,
        CredentialKind.AUTOMATION: AuthenticationMethod.AUTOMATION_TOKEN,
        CredentialKind.INTEGRATION: AuthenticationMethod.INTEGRATION_TOKEN,
    }[kind]


def unrestricted_credential_scope() -> dict[str, JsonValue]:
    return {
        "actions": [],
        "resource_types": [],
        "resource_ids": [],
    }


__all__ = [
    "authentication_now",
    "method_for_kind",
    "parse_secret",
    "secret_verifier",
    "unrestricted_credential_scope",
    "validate_actor_reference",
    "validate_credential_kind",
]
