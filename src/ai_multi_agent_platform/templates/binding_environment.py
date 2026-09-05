"""Server-owned values used to materialize reusable Template configuration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from ai_multi_agent_platform.contracts.types import FrozenJsonValue
from ai_multi_agent_platform.security import SecretReference

from .service import TemplateEnvironment


@dataclass(frozen=True, slots=True)
class MaterializingTemplateEnvironment(TemplateEnvironment):
    """Compatibility environment plus values resolved by trusted platform providers."""

    placeholder_bindings: Mapping[str, FrozenJsonValue] = field(default_factory=dict)
    secret_reference_bindings: Mapping[str, SecretReference] = field(default_factory=dict)
    configuration_payloads: Mapping[str, Mapping[str, FrozenJsonValue]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        super().__post_init__()
        ordinary = dict(self.placeholder_bindings)
        references = dict(self.secret_reference_bindings)
        configurations = {
            reference: MappingProxyType(dict(payload))
            for reference, payload in self.configuration_payloads.items()
        }
        _validate_keys(ordinary, "placeholder binding")
        _validate_keys(references, "secret-reference binding")
        _validate_keys(configurations, "configuration reference")
        object.__setattr__(self, "placeholder_bindings", MappingProxyType(ordinary))
        object.__setattr__(self, "secret_reference_bindings", MappingProxyType(references))
        object.__setattr__(self, "configuration_payloads", MappingProxyType(configurations))


def _validate_keys(values: Mapping[str, object], label: str) -> None:
    if any(not key.strip() for key in values):
        raise ValueError(f"Template {label} names must be non-blank")
