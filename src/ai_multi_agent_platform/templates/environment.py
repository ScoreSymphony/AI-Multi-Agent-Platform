"""Server-owned resolution of Template compatibility inputs and materialization bindings.

Clients never declare their own compatibility environment. This resolver composes only
canonical inventory/value sources whose semantics match Template requirements exactly. Unknown
inventories stay empty so preview/apply fail conservatively rather than assuming support.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass

from ai_multi_agent_platform.contracts.types import FrozenJsonValue
from ai_multi_agent_platform.control_plane.models import RequestContext
from ai_multi_agent_platform.security import SecretReference
from ai_multi_agent_platform.workspaces import WorkspaceProvider

from .binding_environment import MaterializingTemplateEnvironment
from .service import TemplateEnvironment

InventoryProvider = Callable[[], Iterable[str]]
ScopedInventoryProvider = Callable[[RequestContext], Iterable[str]]
CapabilityVersionProvider = Callable[[], Iterable[tuple[str, str]]]
ContractVersionProvider = Callable[[], Mapping[str, str]]
PlaceholderBindingProvider = Callable[[RequestContext], Mapping[str, FrozenJsonValue]]
SecretReferenceBindingProvider = Callable[[RequestContext], Mapping[str, SecretReference]]
ConfigurationPayloadProvider = Callable[
    [RequestContext],
    Mapping[str, Mapping[str, FrozenJsonValue]],
]


@dataclass(slots=True)
class PlatformTemplateEnvironmentResolver:
    """Resolve deployment compatibility and safe bindings from canonical server state."""

    workspaces: WorkspaceProvider | None = None
    capabilities: InventoryProvider | None = None
    capability_versions: CapabilityVersionProvider | None = None
    plugins: InventoryProvider | None = None
    connectors: InventoryProvider | None = None
    model_policies: InventoryProvider | None = None
    grantable_permissions: ScopedInventoryProvider | None = None
    placeholders: ScopedInventoryProvider | None = None
    secret_reference_placeholders: ScopedInventoryProvider | None = None
    validated_configuration_refs: ScopedInventoryProvider | None = None
    placeholder_bindings: PlaceholderBindingProvider | None = None
    secret_reference_bindings: SecretReferenceBindingProvider | None = None
    configuration_payloads: ConfigurationPayloadProvider | None = None
    platform_version: str | None = None
    contract_versions: ContractVersionProvider | None = None

    async def resolve(self, context: RequestContext) -> TemplateEnvironment:
        workspace_ids: frozenset[str] = frozenset()
        if self.workspaces is not None:
            workspaces = await self.workspaces.list_workspaces()
            owner_type = context.actor.owner_type
            owner_id = context.actor.owner_id
            workspace_ids = frozenset(
                workspace.id
                for workspace in workspaces
                if owner_type is not None
                and owner_id is not None
                and workspace.owner_ref.type == owner_type
                and workspace.owner_ref.id == owner_id
            )

        capability_versions = _capability_version_inventory(self.capability_versions)
        capability_ids = _inventory(self.capabilities) | frozenset(capability_versions)
        ordinary_bindings = _placeholder_bindings(self.placeholder_bindings, context)
        secret_bindings = _secret_reference_bindings(self.secret_reference_bindings, context)
        configuration_payloads = _configuration_payloads(self.configuration_payloads, context)
        resolved_placeholders = _scoped_inventory(self.placeholders, context) | frozenset(
            ordinary_bindings
        )
        resolved_secret_placeholders = _scoped_inventory(
            self.secret_reference_placeholders,
            context,
        ) | frozenset(secret_bindings)
        validated_configuration_refs = _scoped_inventory(
            self.validated_configuration_refs,
            context,
        ) | frozenset(configuration_payloads)

        return MaterializingTemplateEnvironment(
            capability_ids=capability_ids,
            capability_versions=capability_versions,
            plugin_ids=_inventory(self.plugins),
            connector_ids=_inventory(self.connectors),
            model_policy_refs=_inventory(self.model_policies),
            grantable_permissions=_scoped_inventory(self.grantable_permissions, context),
            workspace_prerequisites=workspace_ids,
            resolved_placeholders=resolved_placeholders,
            resolved_secret_reference_placeholders=resolved_secret_placeholders,
            validated_configuration_refs=validated_configuration_refs,
            placeholder_bindings=ordinary_bindings,
            secret_reference_bindings=secret_bindings,
            configuration_payloads=configuration_payloads,
            platform_version=self.platform_version,
            contract_versions=_contract_version_inventory(self.contract_versions),
        )


def _inventory(provider: InventoryProvider | None) -> frozenset[str]:
    if provider is None:
        return frozenset()
    return _validated_ids(provider())


def _scoped_inventory(
    provider: ScopedInventoryProvider | None,
    context: RequestContext,
) -> frozenset[str]:
    if provider is None:
        return frozenset()
    return _validated_ids(provider(context))


def _validated_ids(values: Iterable[str]) -> frozenset[str]:
    result = frozenset(values)
    if any(not isinstance(value, str) or not value.strip() for value in result):
        raise ValueError("Template environment inventory IDs must be non-blank strings")
    return result


def _capability_version_inventory(
    provider: CapabilityVersionProvider | None,
) -> dict[str, tuple[str, ...]]:
    if provider is None:
        return {}
    collected: dict[str, set[str]] = {}
    for capability_id, version in provider():
        if not isinstance(capability_id, str) or not capability_id.strip():
            raise ValueError("Template capability version inventory IDs must be non-blank")
        if not isinstance(version, str) or not version.strip():
            raise ValueError("Template capability inventory versions must be non-blank")
        collected.setdefault(capability_id, set()).add(version)
    return {
        capability_id: tuple(sorted(versions))
        for capability_id, versions in sorted(collected.items())
    }


def _contract_version_inventory(
    provider: ContractVersionProvider | None,
) -> dict[str, str]:
    if provider is None:
        return {}
    result = dict(provider())
    if any(
        not isinstance(name, str)
        or not name.strip()
        or not isinstance(version, str)
        or not version.strip()
        for name, version in result.items()
    ):
        raise ValueError("Template contract version inventory must use non-blank strings")
    return result


def _placeholder_bindings(
    provider: PlaceholderBindingProvider | None,
    context: RequestContext,
) -> dict[str, FrozenJsonValue]:
    if provider is None:
        return {}
    result = dict(provider(context))
    _validate_binding_keys(result, "placeholder")
    return result


def _secret_reference_bindings(
    provider: SecretReferenceBindingProvider | None,
    context: RequestContext,
) -> dict[str, SecretReference]:
    if provider is None:
        return {}
    result = dict(provider(context))
    _validate_binding_keys(result, "secret-reference placeholder")
    if any(not isinstance(value, SecretReference) for value in result.values()):
        raise ValueError("Template secret-reference bindings must use SecretReference values")
    return result


def _configuration_payloads(
    provider: ConfigurationPayloadProvider | None,
    context: RequestContext,
) -> dict[str, Mapping[str, FrozenJsonValue]]:
    if provider is None:
        return {}
    result = dict(provider(context))
    _validate_binding_keys(result, "configuration reference")
    if any(not isinstance(payload, Mapping) for payload in result.values()):
        raise ValueError("Template configuration reference payloads must be objects")
    return result


def _validate_binding_keys(values: Mapping[str, object], label: str) -> None:
    if any(not isinstance(key, str) or not key.strip() for key in values):
        raise ValueError(f"Template {label} names must be non-blank strings")
