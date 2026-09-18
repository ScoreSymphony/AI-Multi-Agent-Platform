"""Extensible Marketplace component-kind contracts."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .models import DistributionRoute, RegistryItemKind, RegistryItemType, registry_item_kind_value


@dataclass(frozen=True, slots=True)
class MarketplaceKindDescriptor:
    kind: RegistryItemKind
    display_name: str
    default_route: DistributionRoute
    supports_install: bool = True
    supports_update: bool = True
    supports_uninstall: bool = True
    group: str = "other"
    owner_resource: str | None = None
    management_path: str | None = None

    def __post_init__(self) -> None:
        normalized = registry_item_kind_value(self.kind)
        try:
            resolved: RegistryItemKind = RegistryItemType(normalized)
        except ValueError:
            resolved = normalized
        object.__setattr__(self, "kind", resolved)
        if not self.display_name.strip():
            raise ValueError("marketplace kind display_name must be non-blank")

    @property
    def kind_value(self) -> str:
        return registry_item_kind_value(self.kind)


class MarketplaceKindRegistry:
    """Registry of Marketplace kind metadata, independent from owner-domain runtimes."""

    def __init__(self, descriptors: Iterable[MarketplaceKindDescriptor] = ()) -> None:
        self._descriptors: dict[str, MarketplaceKindDescriptor] = {}
        for descriptor in descriptors:
            self.register(descriptor)

    def register(self, descriptor: MarketplaceKindDescriptor) -> None:
        kind = descriptor.kind_value
        if kind in self._descriptors:
            raise ValueError(f"marketplace kind {kind!r} is already registered")
        self._descriptors[kind] = descriptor

    def get(self, kind: RegistryItemKind) -> MarketplaceKindDescriptor | None:
        return self._descriptors.get(registry_item_kind_value(kind))

    def require(self, kind: RegistryItemKind) -> MarketplaceKindDescriptor:
        normalized = registry_item_kind_value(kind)
        descriptor = self._descriptors.get(normalized)
        if descriptor is None:
            raise KeyError(f"marketplace kind {normalized!r} is not registered")
        return descriptor

    def list(self) -> tuple[MarketplaceKindDescriptor, ...]:
        return tuple(self._descriptors[key] for key in sorted(self._descriptors))


BUILTIN_MARKETPLACE_KINDS: tuple[MarketplaceKindDescriptor, ...] = (
    MarketplaceKindDescriptor(
        RegistryItemType.AGENT,
        "Agent",
        DistributionRoute.KIND_HANDLER,
        group="ai_agents",
        owner_resource="agents",
        management_path="/agents",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.AGENT_TEAM,
        "Agent Team",
        DistributionRoute.KIND_HANDLER,
        group="ai_agents",
        owner_resource="agent-teams",
        management_path="/agent-teams",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.ORCHESTRATOR,
        "Orchestrator",
        DistributionRoute.KIND_HANDLER,
        group="ai_agents",
        owner_resource="plugins",
        management_path="/plugins",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.EXECUTOR,
        "Executor",
        DistributionRoute.KIND_HANDLER,
        group="platform_extensions",
        owner_resource="plugins",
        management_path="/plugins",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.MODEL_PROVIDER,
        "Model Provider",
        DistributionRoute.KIND_HANDLER,
        group="models",
        owner_resource="plugins",
        management_path="/models",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.CAPABILITY_PROVIDER,
        "Capability Provider",
        DistributionRoute.KIND_HANDLER,
        group="tools_integrations",
        owner_resource="plugins",
        management_path="/plugins",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.MEMORY_PROVIDER,
        "Memory Provider",
        DistributionRoute.KIND_HANDLER,
        group="platform_extensions",
        owner_resource="plugins",
        management_path="/plugins",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.FILE_PROVIDER,
        "File / Storage Provider",
        DistributionRoute.KIND_HANDLER,
        group="platform_extensions",
        owner_resource="plugins",
        management_path="/plugins",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.KNOWLEDGE_PROVIDER,
        "Knowledge Provider",
        DistributionRoute.KIND_HANDLER,
        group="platform_extensions",
        owner_resource="plugins",
        management_path="/plugins",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.OBSERVABILITY_EXPORTER,
        "Observability Exporter",
        DistributionRoute.KIND_HANDLER,
        group="platform_extensions",
        owner_resource="plugins",
        management_path="/plugins",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.AUTOMATION_PROVIDER,
        "Automation Provider",
        DistributionRoute.KIND_HANDLER,
        group="platform_extensions",
        owner_resource="plugins",
        management_path="/plugins",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.EVALUATOR,
        "Evaluator",
        DistributionRoute.KIND_HANDLER,
        group="platform_extensions",
        owner_resource="plugins",
        management_path="/plugins",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.TOOL,
        "Tool",
        DistributionRoute.PORTABLE_IMPORT,
        group="tools_integrations",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.SKILL,
        "Skill",
        DistributionRoute.KIND_HANDLER,
        group="ai_agents",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.PLUGIN,
        "Plugin",
        DistributionRoute.PLUGIN,
        group="platform_extensions",
        owner_resource="plugins",
        management_path="/plugins",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.WORKFLOW,
        "Workflow",
        DistributionRoute.PORTABLE_IMPORT,
        supports_install=False,
        supports_update=False,
        supports_uninstall=False,
        group="content",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.TEMPLATE,
        "Template",
        DistributionRoute.PORTABLE_IMPORT,
        supports_install=False,
        supports_update=False,
        supports_uninstall=False,
        group="content",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.MODEL_CONFIGURATION,
        "Model Configuration",
        DistributionRoute.PORTABLE_IMPORT,
        group="models",
        owner_resource="models",
        management_path="/models",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.CONNECTOR,
        "Connector",
        DistributionRoute.PORTABLE_IMPORT,
        group="tools_integrations",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.APPLICATION,
        "Application",
        DistributionRoute.KIND_HANDLER,
        supports_update=False,
        group="applications",
        owner_resource="applications",
        management_path="/applications",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.EVALUATION,
        "Evaluation",
        DistributionRoute.PORTABLE_IMPORT,
        group="platform_extensions",
    ),
    MarketplaceKindDescriptor(
        RegistryItemType.DOCUMENTATION,
        "Documentation",
        DistributionRoute.MANUAL,
        supports_install=False,
        supports_update=False,
        supports_uninstall=False,
        group="content",
    ),
)

_BUILTIN_BY_KIND = {descriptor.kind_value: descriptor for descriptor in BUILTIN_MARKETPLACE_KINDS}


def builtin_marketplace_kind(kind: RegistryItemKind) -> MarketplaceKindDescriptor | None:
    return _BUILTIN_BY_KIND.get(registry_item_kind_value(kind))


def marketplace_kind_registry_with_builtins() -> MarketplaceKindRegistry:
    return MarketplaceKindRegistry(BUILTIN_MARKETPLACE_KINDS)
