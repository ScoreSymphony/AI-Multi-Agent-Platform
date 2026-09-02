"""Platform configuration, secret-reference and redaction contracts."""
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
from .redaction import REDACTED, redact_exception, redact_text, redact_value
from .secrets import (
    LocalSecretProvider,
    SecretAccessContext,
    SecretAuditEvent,
    SecretMaterial,
    SecretMetadata,
    SecretProvider,
    SecretReference,
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
    "SecretMaterial",
    "SecretMetadata",
    "SecretProvider",
    "SecretReference",
    "SecretState",
    "environment_layer",
    "redact_exception",
    "redact_text",
    "redact_value",
]
