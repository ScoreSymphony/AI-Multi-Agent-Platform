from __future__ import annotations

import asyncio
import json

import pytest

from ai_multi_agent_platform.configuration import (
    ConfigLayer,
    ConfigScope,
    ConfigSource,
    ConfigurationError,
    ConfigurationResolver,
    ConfigurationSchema,
    LocalSecretProvider,
    ReloadRequirement,
    SecretAccessContext,
    SecretAuditEvent,
    SecretReference,
    SecretState,
    environment_layer,
    redact_exception,
    redact_text,
    redact_value,
)
from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import HealthStatus


def _schema() -> ConfigurationSchema:
    return ConfigurationSchema(
        version="1.0",
        json_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "required": ["runtime", "model"],
            "properties": {
                "runtime": {
                    "type": "object",
                    "required": ["workers", "mode"],
                    "properties": {
                        "workers": {"type": "integer", "minimum": 1},
                        "mode": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "model": {
                    "type": "object",
                    "required": ["credential"],
                    "properties": {
                        "credential": {
                            "type": "object",
                            "required": ["secret_ref"],
                        }
                    },
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
        secret_paths=frozenset({"model.credential"}),
        run_override_paths=frozenset({"runtime.workers"}),
        reload_requirements={
            "runtime.workers": ReloadRequirement.RELOAD,
            "runtime.mode": ReloadRequirement.RESTART,
        },
    )


def _reference() -> SecretReference:
    return SecretReference(
        secret_id="secret_model_primary",
        purpose="model-api",
        owner_scope="project:project_demo",
        backend_ref="local:secret_model_primary",
    )


def _base_layer() -> ConfigLayer:
    return ConfigLayer(
        ConfigScope.PLATFORM_DEFAULTS,
        {
            "runtime": {"workers": 1, "mode": "safe"},
            "model": {"credential": _reference()},
        },
        ConfigSource("platform-defaults", "builtin"),
    )


def test_configuration_precedence_and_provenance_are_deterministic() -> None:
    resolver = ConfigurationResolver(_schema())
    effective = resolver.resolve(
        (
            _base_layer(),
            ConfigLayer(
                ConfigScope.DEPLOYMENT,
                {"runtime": {"workers": 2}},
                ConfigSource("deployment", "file"),
            ),
            ConfigLayer(
                ConfigScope.PROJECT,
                {"runtime": {"workers": 3}},
                ConfigSource("project", "database"),
            ),
            ConfigLayer(
                ConfigScope.TASK_RUN_OVERRIDE,
                {"runtime": {"workers": 4}},
                ConfigSource("run", "request"),
            ),
        )
    )

    assert effective.as_dict()["runtime"]["workers"] == 4
    rows = {row["path"]: row for row in effective.inspect()}
    assert [item["scope"] for item in rows["runtime.workers"]["source_chain"]] == [
        "platform_defaults",
        "deployment",
        "project",
        "task_run_override",
    ]


def test_invalid_configuration_fails_before_use() -> None:
    resolver = ConfigurationResolver(_schema())
    with pytest.raises(ConfigurationError, match="configuration validation failed"):
        resolver.resolve(
            (
                ConfigLayer(
                    ConfigScope.PLATFORM_DEFAULTS,
                    {
                        "runtime": {"workers": 0, "mode": "safe"},
                        "model": {"credential": _reference()},
                    },
                    ConfigSource("invalid", "test"),
                ),
            )
        )


def test_plaintext_is_rejected_for_declared_secret_paths() -> None:
    resolver = ConfigurationResolver(_schema())
    with pytest.raises(ConfigurationError, match="SecretReference"):
        resolver.resolve(
            (
                ConfigLayer(
                    ConfigScope.PLATFORM_DEFAULTS,
                    {
                        "runtime": {"workers": 1, "mode": "safe"},
                        "model": {"credential": "plaintext-token"},
                    },
                    ConfigSource("invalid", "test"),
                ),
            )
        )


def test_task_run_override_is_explicitly_allowlisted() -> None:
    resolver = ConfigurationResolver(_schema())
    with pytest.raises(ConfigurationError, match="run override is not allowed"):
        resolver.resolve(
            (
                _base_layer(),
                ConfigLayer(
                    ConfigScope.TASK_RUN_OVERRIDE,
                    {"runtime": {"mode": "unsafe"}},
                    ConfigSource("run", "request"),
                ),
            )
        )


def test_safe_introspection_redacts_secret_and_exposes_reload_metadata() -> None:
    effective = ConfigurationResolver(_schema()).resolve((_base_layer(),))
    serialized = json.dumps(effective.as_dict())
    assert "plaintext-token" not in serialized
    assert "secret_model_primary" in serialized

    rows = {row["path"]: row for row in effective.inspect()}
    secret_row = rows["model.credential"]
    assert secret_row["is_secret"] is True
    assert secret_row["value"]["configured"] is True
    assert secret_row["validation_status"] == "valid"
    assert rows["runtime.workers"]["reload_requirement"] == "reload"
    assert rows["runtime.mode"]["reload_requirement"] == "restart"


def test_environment_layer_is_explicit_and_scoped() -> None:
    layer = environment_layer(
        source_id="deployment-env",
        environ={"APP_MODE": "maintenance", "UNRELATED": "ignored"},
        key_map={"APP_MODE": "runtime.mode"},
    )
    assert layer.scope is ConfigScope.DEPLOYMENT
    assert layer.values == {"runtime": {"mode": "maintenance"}}
    assert layer.source.source_type == "environment"


def test_secret_create_resolve_rotate_and_revoke() -> None:
    async def scenario() -> None:
        provider = LocalSecretProvider()
        reference = _reference()
        metadata = await provider.create(
            reference,
            "alpha-value",
            allowed_consumers=("worker:one",),
            allowed_purposes=("model-api",),
        )
        assert "alpha-value" not in json.dumps(metadata.to_dict())

        material = await provider.resolve(
            reference,
            SecretAccessContext(
                consumer_ref="worker:one",
                project_id="project_demo",
                run_id="run_demo",
                action="model.generate",
                capability_ref="model.primary",
                purpose="model-api",
                requested_lifetime_seconds=120,
            ),
        )
        assert material.reveal() == "alpha-value"
        assert "alpha-value" not in repr(material)

        rotated = await provider.rotate(reference, "beta-value")
        assert rotated.rotation_count == 1
        assert rotated.rotated_at is not None
        assert (await provider.resolve(reference, SecretAccessContext("worker:one"))).reveal() == (
            "beta-value"
        )

        revoked = await provider.revoke(reference)
        assert revoked.state is SecretState.REVOKED
        with pytest.raises(ContractError) as caught:
            await provider.resolve(reference, SecretAccessContext("worker:one"))
        assert caught.value.code is ErrorCode.FORBIDDEN

    asyncio.run(scenario())


def test_missing_secret_and_backend_unavailable_are_canonical_errors() -> None:
    async def scenario() -> None:
        provider = LocalSecretProvider()
        with pytest.raises(ContractError) as missing:
            await provider.metadata(_reference())
        assert missing.value.code is ErrorCode.NOT_FOUND

        provider.set_available(False)
        assert await provider.health() is HealthStatus.UNAVAILABLE
        with pytest.raises(ContractError) as unavailable:
            await provider.metadata(_reference())
        assert unavailable.value.code is ErrorCode.UNAVAILABLE
        assert unavailable.value.retryable is True

    asyncio.run(scenario())


def test_consumer_and_purpose_hooks_enforce_reference_metadata() -> None:
    async def scenario() -> None:
        provider = LocalSecretProvider()
        reference = _reference()
        await provider.create(
            reference,
            "alpha-value",
            allowed_consumers=("worker:allowed",),
            allowed_purposes=("model-api",),
        )
        with pytest.raises(ContractError) as consumer_denied:
            await provider.resolve(reference, SecretAccessContext("worker:other"))
        assert consumer_denied.value.code is ErrorCode.FORBIDDEN

        with pytest.raises(ContractError) as purpose_denied:
            await provider.resolve(
                reference,
                SecretAccessContext("worker:allowed", purpose="connector-api"),
            )
        assert purpose_denied.value.code is ErrorCode.FORBIDDEN

    asyncio.run(scenario())


def test_audit_hook_never_receives_secret_value() -> None:
    events: list[SecretAuditEvent] = []

    async def scenario() -> None:
        provider = LocalSecretProvider(audit_hook=events.append)
        reference = _reference()
        await provider.create(reference, "do-not-log")
        await provider.resolve(reference, SecretAccessContext("worker:one"))
        await provider.rotate(reference, "new-do-not-log")
        await provider.revoke(reference)

    asyncio.run(scenario())
    dumped = repr(events)
    assert "do-not-log" not in dumped
    assert [event.operation for event in events] == ["create", "resolve", "rotate", "revoke"]


def test_redaction_helpers_cover_nested_payloads_text_and_exceptions() -> None:
    payload = {
        "api_key": "visible-by-key",
        "nested": {"message": "provider failed with abc123"},
        "items": ["abc123", {"password": "visible-by-key"}],
    }
    redacted = redact_value(payload, ("abc123",))
    assert redacted == {
        "api_key": "[REDACTED]",
        "nested": {"message": "provider failed with [REDACTED]"},
        "items": ["[REDACTED]", {"password": "[REDACTED]"}],
    }
    assert redact_text("token=abc123", ("abc123",)) == "token=[REDACTED]"
    assert redact_exception(RuntimeError("failed abc123"), ("abc123",)) == "failed [REDACTED]"
