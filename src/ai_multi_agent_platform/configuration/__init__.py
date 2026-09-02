"""Platform configuration and secret-management contracts."""

from ai_multi_agent_platform.security import (
    REDACTED,
    SecretReference,
    redact_exception,
    redact_sensitive,
    redact_text,
)

from .core import (
    CONFIG_PRECEDENCE,
    ConfigLayer,
    ConfigScope,
    ConfigSource,
    ConfigurationError,
    ConfigurationResolver,
    ConfigurationSchema,
    EffectiveConfiguration,
    ReloadRequirement,
    environment_layer,
)
from .secrets import (
    LocalSecretProvider,
    SecretAccessContext,
    SecretAuditEvent,
    SecretAuditSink,
    SecretMaterial,
    SecretMetadata,
    SecretProvider,
    SecretState,
)

__all__ = [
    "CONFIG_PRECEDENCE",
    "REDACTED",
    "ConfigLayer",
    "ConfigScope",
    "ConfigSource",
    "ConfigurationError",
    "ConfigurationResolver",
    "ConfigurationSchema",
    "EffectiveConfiguration",
    "LocalSecretProvider",
    "ReloadRequirement",
    "SecretAccessContext",
    "SecretAuditEvent",
    "SecretAuditSink",
    "SecretMaterial",
    "SecretMetadata",
    "SecretProvider",
    "SecretReference",
    "SecretState",
    "environment_layer",
    "redact_exception",
    "redact_sensitive",
    "redact_text",
]
