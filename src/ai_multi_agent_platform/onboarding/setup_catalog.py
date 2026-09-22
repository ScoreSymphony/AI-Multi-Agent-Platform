"""Product-facing Registry card projection for browser-first setup."""

from __future__ import annotations

from ai_multi_agent_platform.contracts.types import JsonValue

from .components import ComponentLifecycle
from .setup_registry_contracts import SetupRegistryItem


def registry_category(categories: tuple[str, ...]) -> str:
    """Map Registry taxonomy into the stable setup product categories."""

    category_set = set(categories)
    if category_set & {"inference-runtime", "model-and-dataset-tooling"}:
        return "model_provider"
    if "agent-framework" in category_set:
        return "orchestrator"
    if category_set & {"memory-and-context", "retrieval"}:
        return "memory_knowledge"
    if category_set & {"browser-and-execution", "coding-agent"}:
        return "executor"
    return "tools_mcp"


def project_registry_card(
    item: SetupRegistryItem,
    *,
    installed_version: str | None,
    mutation_enabled: bool,
) -> dict[str, JsonValue]:
    """Project one Registry item without mixing presentation policy into setup coordination."""

    technical = item.technical
    blocked = item.deprecated or item.yanked
    manual = item.route == "manual"
    external_runtime_required = bool(technical is not None and technical.external_runtime_required)
    return {
        "id": f"{item.item_id}@{item.version}",
        "kind": "registry_item",
        "display_name": item.name,
        "utility": item.description,
        "category": registry_category(item.categories),
        "install_status": _install_status(
            installed=installed_version is not None,
            blocked=blocked,
            manual=manual,
            mutation_enabled=mutation_enabled,
            external_runtime_required=external_runtime_required,
        ),
        "compatibility": "blocked" if blocked else "compatible",
        "recommendation": _recommendation(
            technical.lifecycle_status if technical is not None else None
        ),
        "dependencies": [dependency.item_id for dependency in item.dependencies],
        "blockers": _blockers(
            blocked=blocked,
            manual=manual,
            external_runtime_required=external_runtime_required,
        ),
        "delivery": _delivery(item),
        "requires_secrets": False,
        "configuration_fields": [],
        "license": item.license,
        "upstream": item.source_repository,
        "version": item.version,
        "technical_id": item.item_id,
    }


def _install_status(
    *,
    installed: bool,
    blocked: bool,
    manual: bool,
    mutation_enabled: bool,
    external_runtime_required: bool,
) -> str:
    if installed:
        return "adapter_installed" if external_runtime_required else "installed"
    if blocked:
        return "blocked"
    if manual or not mutation_enabled:
        return "manual_required"
    return "installable"


def _blockers(
    *,
    blocked: bool,
    manual: bool,
    external_runtime_required: bool,
) -> list[JsonValue]:
    if blocked:
        return ["registry item is deprecated or yanked"]
    if manual:
        return ["manual distribution route"]
    if external_runtime_required:
        return [
            "Adapter installation does not install or start the required external "
            "runtime; configure and validate that service separately."
        ]
    return []


def _delivery(item: SetupRegistryItem) -> str:
    technical = item.technical
    if technical is None:
        return "local"
    if "hosted" in technical.deployment_modes or technical.network_status == "required":
        return "external"
    return "local"


def _recommendation(lifecycle: str | None) -> str:
    if lifecycle in {"adopted", "reference"}:
        return ComponentLifecycle.RECOMMENDED.value
    if lifecycle in {"pilot", "candidate"}:
        return ComponentLifecycle.EXPERIMENTAL.value
    if lifecycle in {"deprecated", "rejected"}:
        return ComponentLifecycle.DEPRECATED.value
    return ComponentLifecycle.SUPPORTED.value


__all__ = ["project_registry_card", "registry_category"]
