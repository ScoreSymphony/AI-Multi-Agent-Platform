"""Safe materialization of portable Template configuration intent.

Template definitions remain immutable and portable. Values from the applying environment are
bound only into ephemeral revision copies immediately before preview/apply handlers consume
them. Secret placeholders become canonical SecretReference metadata and are never resolved to
plaintext here.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Protocol, cast

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import FrozenJsonValue
from ai_multi_agent_platform.security import SecretReference

from .models import TemplateConfiguration, TemplateRevision

_PLACEHOLDER = re.compile(r"\$\{([^{}]+)\}")


class TemplateBindingEnvironment(Protocol):
    placeholder_bindings: Mapping[str, FrozenJsonValue]
    secret_reference_bindings: Mapping[str, SecretReference]
    configuration_payloads: Mapping[str, Mapping[str, FrozenJsonValue]]


def materialize_template_revision(
    revision: TemplateRevision,
    environment: TemplateBindingEnvironment,
) -> TemplateRevision:
    """Return an ephemeral revision whose configuration is ready for a canonical handler.

    Ordinary placeholders may bind any JSON-compatible value when the complete scalar is the
    placeholder. Embedded interpolation is deliberately limited to string values. Secret
    placeholders must occupy the complete scalar and are replaced by ``SecretReference.to_dict``;
    this keeps plaintext outside Template state and downstream handler inputs.
    """

    configuration = revision.content.configuration
    if configuration.reference is not None:
        payload = environment.configuration_payloads.get(configuration.reference)
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
        environment,
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
    return replace(revision, content=content)


def _materialize_value(
    value: FrozenJsonValue | Mapping[str, FrozenJsonValue],
    environment: TemplateBindingEnvironment,
    *,
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
                    environment,
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
                environment,
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
            reference = environment.secret_reference_bindings.get(name)
            if reference is None:
                raise ContractError(
                    ErrorCode.INVALID_CONFIGURATION,
                    "Template secret-reference placeholder has no canonical binding",
                    details={"placeholder": name, "path": path},
                )
            return cast(FrozenJsonValue, reference.to_dict())
        if name not in environment.placeholder_bindings:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Template placeholder has no materialized binding",
                details={"placeholder": name, "path": path},
            )
        return environment.placeholder_bindings[name]

    for match in matches:
        name = match.group(1)
        if name in secret:
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "SecretReference placeholders cannot be interpolated into strings",
                details={"placeholder": name, "path": path},
            )
        replacement = environment.placeholder_bindings.get(name)
        if not isinstance(replacement, str):
            raise ContractError(
                ErrorCode.INVALID_CONFIGURATION,
                "Embedded Template placeholders require a string binding",
                details={"placeholder": name, "path": path},
            )
        value = value.replace(match.group(0), replacement)
    return value
