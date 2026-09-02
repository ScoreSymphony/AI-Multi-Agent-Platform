from __future__ import annotations

import asyncio
import json

import pytest

from ai_multi_agent_platform.configuration import (
    LocalSecretProvider,
    SecretAccessContext,
    SecretAuditEvent,
    SecretProvider,
    SecretReference,
    redact_sensitive,
    redact_text,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode


def _reference() -> SecretReference:
    return SecretReference(
        provider="local-secrets",
        secret_id="secret_issue34_completion",
        scope="project:project_demo",
        version="1",
    )


def test_secret_provider_contract_requires_audit_hook_boundary() -> None:
    assert "set_audit_hook" in SecretProvider.__abstractmethods__


def test_local_secret_provider_supports_replaceable_audit_sink() -> None:
    events: list[SecretAuditEvent] = []

    async def scenario() -> None:
        provider = LocalSecretProvider()
        provider.set_audit_hook(events.append)
        reference = _reference()
        await provider.create(reference, "audit-secret", purpose="model-api")
        await provider.resolve(reference, SecretAccessContext("worker:one"))
        provider.set_audit_hook(None)
        await provider.rotate(reference, "rotated-secret")

    asyncio.run(scenario())

    assert [event.operation for event in events] == ["create", "resolve"]
    serialized = repr(events)
    assert "audit-secret" not in serialized
    assert "rotated-secret" not in serialized


def test_local_reference_backend_declares_ephemeral_restart_contract() -> None:
    provider = LocalSecretProvider()
    assert provider.descriptor.resources == {
        "storage": "memory_only",
        "durable": False,
        "restart_requires_reprovision": True,
    }


def test_local_reference_backend_restart_requires_reprovision() -> None:
    async def scenario() -> None:
        reference = _reference()
        first_process = LocalSecretProvider()
        await first_process.create(reference, "process-local-secret", purpose="model-api")

        restarted_process = LocalSecretProvider()
        with pytest.raises(ContractError) as caught:
            await restarted_process.metadata(reference)
        assert caught.value.code is ErrorCode.NOT_FOUND

    asyncio.run(scenario())


def test_redaction_helpers_cover_representative_operational_api_and_export_surfaces() -> None:
    secret = "issue34-sensitive-value"
    structured_surfaces = {
        "log": {"authorization": secret, "status": "failed"},
        "event": {"token": secret, "event_type": "provider.failure"},
        "trace": {"client_secret": secret, "span": "provider.resolve"},
        "api_response": {"api_key": secret, "configured": True},
        "export": {"password": secret, "format": "json"},
        "evaluation": {"private_key": secret, "score": 1},
        "diagnostic": {"credential": secret, "healthy": False},
    }
    redacted = redact_sensitive(structured_surfaces)
    dumped = json.dumps(redacted, sort_keys=True)
    assert secret not in dumped
    assert dumped.count("[REDACTED]") == len(structured_surfaces)

    textual_surfaces = (
        f"exception failed with {secret}",
        f"prompt accidentally contained {secret}",
        f"log message accidentally contained {secret}",
        f"event detail accidentally contained {secret}",
    )
    for text in textual_surfaces:
        assert secret not in redact_text(text, (secret,))
