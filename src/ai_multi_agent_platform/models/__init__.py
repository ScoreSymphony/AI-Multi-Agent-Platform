"""Platform-owned model inventory, routing, persistence and runtime subsystem."""

from .persistence import MODEL_REGISTRY_SCHEMA_VERSION, JsonModelRegistryStore
from .registry import ModelRegistry
from .router import DeterministicModelRouter
from .runtime import ModelRuntime
from .types import (
    ModelCapabilities,
    ModelConfiguration,
    ModelLocation,
    ModelRoute,
    RoutingRequirements,
)

__all__ = [
    "MODEL_REGISTRY_SCHEMA_VERSION",
    "DeterministicModelRouter",
    "JsonModelRegistryStore",
    "ModelCapabilities",
    "ModelConfiguration",
    "ModelLocation",
    "ModelRegistry",
    "ModelRoute",
    "ModelRuntime",
    "RoutingRequirements",
]
