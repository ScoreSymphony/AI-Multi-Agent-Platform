"""Safe materialization of portable Template configuration intent.

Template definitions remain immutable and portable. Values from the applying environment are
bound only into ephemeral revision copies immediately before canonical handlers consume them.
Secret placeholders become canonical SecretReference metadata and are never resolved to
plaintext here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import FrozenJsonValue
from ai_multi_agent_platform.security import SecretReference

from .models import TemplateConfiguration, TemplateRevision
from .service import TemplateEnvironment, validate_template_configuration

_PLACEHOLDER = re.compile(r"\$\{([^{}]+)\}")


@dataclass(frozen=True, slots=True)
class MaterializingTemplateEnvironment(TemplateEnvironment):
    """TemplateEnvironment carrying server-owned values required for apply materialization.

    Concrete binding mappings are authoritative. The inherited resolved-name/reference sets are
    derived from those mappings so Preview cannot claim materializability from names alone.
    Secret bindings are references, never secret material.
    """

    placeholder_bindings: Mapping[str, FrozenJsonValue] = field(default_factory=dict)
    secret_reference_bindings: Mapping[str, SecretReference] = field(default_factory=dict)
    configuration_payloads: Mapping[str, Mapping[str, FrozenJsonValue]] = field(
        default_factory=dict
    )

    def __post_init__(self) -> None:
        TemplateEnvironment.__post_init__(self)
        placeholders = dict(self.placeholder_bindings)
        secret_references = dict(self.secret_reference_bindings)
        configuration_payloads = {
            reference: MappingProxyType(dict(payload))
            for reference, payload in self.configuration_payloads.items()
        }
        if any(not name.strip() for name in placeholders):
            raise ValueError("Template placeholder binding names must be non-blank")
        if any(not name.strip() for name in secret_references):
            raise ValueError("Template secret-reference binding names must be non-blank")
        if any(not reference.strip() for reference in configuration_payloads):
            raise ValueError("Template configuration reference bindings must be non-blank")
        object.__setattr__(self, "resolved_placeholders", frozenset(placeholders))
        object.__setattr__(
            self,
            "resolved_secret_reference_placeholders",
            frozenset(secret_references),
        )
        object.__setattr__(
            self,
            "validated_configuration_refs",
            frozenset(configuration_payloads),
        )
        object.__setattr__(self, "placeholder_bindings", MappingProxyType(placeholders))
        object.__setattr__(
            self,
            "secret_reference_bindings",
            MappingProxyType(secret_references),
        )
        object.__setattr__(
            self,
            "configuration_payloads",
            MappingProxyType(configuration_payloads),
        )


def materialize_template_revision(
    revision: TemplateRevision,
    environment: TemplateEnvironment,
) -> TemplateRevision:
    """Return an ephemeral revision whose configuration is ready for a canonical handler.

    Ordinary placeholders may bind any JSON-compatible value when the complete scalar is the
    placeholder. Embedded interpolation is deliberately limited to string values. Secret
    placeholders must occupy the complete scalar and are replaced by ``SecretReference.to_dict``;
    this keeps plaintext outside Template state and downstream handler inputs.
    """

    placeholder_bindings = _placeholder_bindings(environment)
    secret_reference_bindings = _secret_reference_bindings(environment)
    configuration_payloads = _configuration_payloads(environment)

    configuration = revision.content.configuration
    if configuration.reference is not None:
        payload = configuration_payloads.get(configuration.reference)
        if payload is None:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "validated Template configuration reference has no materialized payload",
                details={
                    "template_id": revision.template_id,
                    "revision": revision.revision,
                    "configuration_reference": configuration.reference,
                },
            )
        source_payload: Mapping[str, FrozenJsonValue] = payload
    else:
        source_payload = configuration.payload or {}

    ordinary = set(revision.content.requirements.placeholders)
    secret = set(revision.content.requirements.secret_reference_placeholders)
    materialized = _materialize_value(
        source_payload,
        placeholder_bindings=placeholder_bindings,
        secret_reference_bindings=secret_reference_bindings,
        ordinary=ordinary,
        secret=secret,
        path="configuration",
    )
    if not isinstance(materialized, Mapping):
        raise ContractError(
            ErrorCode.CONTRACT_VIOLATION,
            "Template configuration materialization did not produce an object",
        )

    content = replace(
        revision.content,
        configuration=TemplateConfiguration(
            payload=cast(Mapping[str, FrozenJsonValue], materialized)
        ),
    )
    validate_template_configuration(content.configuration)
    return replace(revision, content=content)


def _materialize_value(
    value: FrozenJsonValue | Mapping[str, FrozenJsonValue],
    *,
    placeholder_bindings: Mapping[str, FrozenJsonValue],
    secret_reference_bindings: Mapping[str, SecretReference],
    ordinary: set[str],
    secret: set[str],
    path: str,
) -> FrozenJsonValue:
    if isinstance(value, Mapping):
        return cast(
            FrozenJsonValue,
            {
                str(key): _materialize_value(
                    item,
                    placeholder_bindings=placeholder_bindings,
                    secret_reference_bindings=secret_reference_bindings,
                    ordinary=ordinary,
                    secret=secret,
                    path=f"{path}.{key}",
                )
                for key, item in value.items()
            },
        )
    if isinstance(value, tuple):
        return tuple(
            _materialize_value(
                item,
                placeholder_bindings=placeholder_bindings,
                secret_reference_bindings=secret_reference_bindings,
                ordinary=ordinary,
                secret=secret,
                path=f"{path}[{index}]",
            )
            for index, item in enumerate(value)
        )
    if not isinstance(value, str):
        return value

    matches = tuple(_PLACEHOLDER.finditer(value))
    if not matches:
        return value

    for match in matches:
        name = match.group(1)
        if name not in ordinary and name not in secret:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Template configuration contains an undeclared placeholder",
                details={"placeholder": name, "path": path},
            )

    exact = _PLACEHOLDER.fullmatch(value)
    if exact is not None:
        name = exact.group(1)
        if name in secret:
            reference = secret_reference_bindings.get(name)
            if reference is None:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "Template secret-reference placeholder has no canonical binding",
                    details={"placeholder": name, "path": path},
                )
            return cast(FrozenJsonValue, reference.to_dict())
        if name not in placeholder_bindings:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Template placeholder has no materialized binding",
                details={"placeholder": name, "path": path},
            )
        return placeholder_bindings[name]

    for match in matches:
        name = match.group(1)
        if name in secret:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "SecretReference placeholders cannot be interpolated into strings",
                details={"placeholder": name, "path": path},
            )
        replacement = placeholder_bindings.get(name)
        if not isinstance(replacement, str):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Embedded Template placeholders require a string binding",
                details={"placeholder": name, "path": path},
            )
        value = value.replace(match.group(0), replacement)
    return value


def _placeholder_bindings(environment: TemplateEnvironment) -> Mapping[str, FrozenJsonValue]:
    values = getattr(environment, "placeholder_bindings", None)
    return values if isinstance(values, Mapping) else {}


def _secret_reference_bindings(
    environment: TemplateEnvironment,
) -> Mapping[str, SecretReference]:
    values = getattr(environment, "secret_reference_bindings", None)
    return values if isinstance(values, Mapping) else {}


def _configuration_payloads(
    environment: TemplateEnvironment,
) -> Mapping[str, Mapping[str, FrozenJsonValue]]:
    values = getattr(environment, "configuration_payloads", None)
    return values if isinstance(values, Mapping) else {}
