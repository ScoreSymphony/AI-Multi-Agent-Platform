"""Platform-owned model/provider inventory service."""

from __future__ import annotations

from dataclasses import replace

from ai_multi_agent_platform.contracts.errors import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.interfaces import ModelProvider
from ai_multi_agent_platform.contracts.types import HealthStatus

from .types import ModelConfiguration


class ModelRegistry:
    """Own canonical model inventory independently from provider execution.

    Provider objects are runtime integrations. Model configurations are stable
    platform resources and intentionally survive provider removal so a provider
    can be replaced without rewriting Agent/Task definitions.
    """

    def __init__(self) -> None:
        self._providers: dict[str, ModelProvider] = {}
        self._provider_health: dict[str, HealthStatus] = {}
        self._models: dict[str, ModelConfiguration] = {}
        self._aliases: dict[str, str] = {}

    def register_provider(self, provider: ModelProvider) -> None:
        provider_id = provider.descriptor.provider_id
        current = self._providers.get(provider_id)
        if current is provider:
            return
        if current is not None:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"model provider already registered: {provider_id}",
                provider_id=provider_id,
            )
        self._providers[provider_id] = provider
        self._provider_health[provider_id] = provider.descriptor.health

    def replace_provider(self, provider: ModelProvider) -> None:
        """Replace one provider instance while preserving its canonical models."""

        provider_id = provider.descriptor.provider_id
        self._providers[provider_id] = provider
        self._provider_health[provider_id] = provider.descriptor.health

    def unregister_provider(self, provider_id: str) -> ModelProvider:
        try:
            provider = self._providers.pop(provider_id)
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"model provider not registered: {provider_id}",
                provider_id=provider_id,
            ) from exc
        self._provider_health.pop(provider_id, None)
        return provider

    def get_provider(self, provider_id: str) -> ModelProvider:
        try:
            return self._providers[provider_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.UNAVAILABLE,
                f"model provider is not currently registered: {provider_id}",
                provider_id=provider_id,
            ) from exc

    def list_providers(self) -> tuple[ModelProvider, ...]:
        return tuple(self._providers[key] for key in sorted(self._providers))

    def register_model(self, config: ModelConfiguration) -> ModelConfiguration:
        """Register canonical inventory even when its runtime provider is offline.

        Allowing an inactive provider reference is intentional: persisted model
        inventory and canonical Agent definitions must survive endpoint or node
        removal. The router marks such entries unavailable until the provider is
        attached again.
        """

        current = self._models.get(config.config_id)
        if current is not None:
            if current == config:
                return current
            raise ContractError(
                ErrorCode.CONFLICT,
                f"model configuration ID already exists: {config.config_id}",
                provider_id=config.provider_id,
            )

        if config.config_id in self._aliases:
            raise ContractError(
                ErrorCode.CONFLICT,
                f"model configuration ID conflicts with alias: {config.config_id}",
            )

        self._assert_aliases_available(config.aliases, config.config_id)
        self._models[config.config_id] = config
        for alias in config.aliases:
            self._aliases[alias] = config.config_id
        return config

    def update_model(self, config: ModelConfiguration) -> ModelConfiguration:
        current = self._models.get(config.config_id)
        if current is None:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"model configuration not found: {config.config_id}",
            )
        if config.revision <= current.revision:
            raise ContractError(
                ErrorCode.CONFLICT,
                "model configuration revision must increase",
                details={
                    "model_config_id": config.config_id,
                    "current_revision": current.revision,
                    "new_revision": config.revision,
                },
            )

        old_aliases = current.aliases
        for alias in old_aliases:
            self._aliases.pop(alias, None)
        try:
            self._assert_aliases_available(config.aliases, config.config_id)
        except Exception:
            for alias in old_aliases:
                self._aliases[alias] = current.config_id
            raise

        self._models[config.config_id] = config
        for alias in config.aliases:
            self._aliases[alias] = config.config_id
        return config

    def unregister_model(self, model_id_or_alias: str) -> ModelConfiguration:
        config = self.get_model(model_id_or_alias)
        del self._models[config.config_id]
        for alias in config.aliases:
            self._aliases.pop(alias, None)
        return config

    def set_enabled(self, model_id_or_alias: str, enabled: bool) -> ModelConfiguration:
        current = self.get_model(model_id_or_alias)
        updated = replace(current, enabled=enabled, revision=current.revision + 1)
        self._models[current.config_id] = updated
        return updated

    def set_model_health(
        self,
        model_id_or_alias: str,
        health: HealthStatus,
    ) -> ModelConfiguration:
        current = self.get_model(model_id_or_alias)
        updated = replace(current, health=health, revision=current.revision + 1)
        self._models[current.config_id] = updated
        return updated

    def get_model(self, model_id_or_alias: str) -> ModelConfiguration:
        config_id = self._aliases.get(model_id_or_alias, model_id_or_alias)
        try:
            return self._models[config_id]
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"model configuration not found: {model_id_or_alias}",
            ) from exc

    def list_models(
        self,
        *,
        provider_id: str | None = None,
        enabled: bool | None = None,
    ) -> tuple[ModelConfiguration, ...]:
        models = self._models.values()
        if provider_id is not None:
            models = (item for item in models if item.provider_id == provider_id)
        if enabled is not None:
            models = (item for item in models if item.enabled is enabled)
        return tuple(sorted(models, key=lambda item: item.config_id))

    def provider_health(self, provider_id: str) -> HealthStatus:
        if provider_id not in self._providers:
            return HealthStatus.UNAVAILABLE
        return self._provider_health.get(provider_id, HealthStatus.UNKNOWN)

    def effective_health(self, config: ModelConfiguration) -> HealthStatus:
        provider = self._providers.get(config.provider_id)
        if provider is None or not provider.descriptor.available:
            return HealthStatus.UNAVAILABLE

        provider_health = self.provider_health(config.provider_id)
        if (
            provider_health is HealthStatus.UNAVAILABLE
            or config.health is HealthStatus.UNAVAILABLE
        ):
            return HealthStatus.UNAVAILABLE
        if provider_health is HealthStatus.UNKNOWN or config.health is HealthStatus.UNKNOWN:
            return HealthStatus.UNKNOWN
        if (
            provider_health is HealthStatus.DEGRADED
            or config.health is HealthStatus.DEGRADED
        ):
            return HealthStatus.DEGRADED
        return HealthStatus.HEALTHY

    async def refresh_health(
        self,
        provider_id: str | None = None,
    ) -> dict[str, HealthStatus]:
        if provider_id is not None:
            provider = self.get_provider(provider_id)
            self._provider_health[provider_id] = await provider.health()
            return {provider_id: self._provider_health[provider_id]}

        for current_id, provider in self._providers.items():
            self._provider_health[current_id] = await provider.health()
        return dict(self._provider_health)

    def _assert_aliases_available(
        self,
        aliases: tuple[str, ...],
        config_id: str,
    ) -> None:
        for alias in aliases:
            existing_id = self._aliases.get(alias)
            if alias in self._models and alias != config_id:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"model alias already registered as canonical ID: {alias}",
                )
            if existing_id is not None and existing_id != config_id:
                raise ContractError(
                    ErrorCode.CONFLICT,
                    f"model alias already registered: {alias}",
                )
