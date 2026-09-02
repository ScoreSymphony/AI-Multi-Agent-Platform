"""Platform-owned model inventory, routing, persistence and runtime subsystem."""

from .persistence import MODEL_REGISTRY_SCHEMA_VERSION, JsonModelRegistryStore
from .protocol import (
    CanonicalModelRequest,
    CanonicalModelResponse,
    ModelContentBlock,
    ModelContentKind,
    ModelFinishReason,
    ModelGenerationParameters,
    ModelMessage,
    ModelRole,
    ModelTiming,
    ModelToolCallRequest,
    ModelToolDefinition,
    StructuredResponseExpectation,
    StructuredResponseKind,
)
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
    "CanonicalModelRequest",
    "CanonicalModelResponse",
    "DeterministicModelRouter",
    "JsonModelRegistryStore",
    "ModelCapabilities",
    "ModelConfiguration",
    "ModelContentBlock",
    "ModelContentKind",
    "ModelFinishReason",
    "ModelGenerationParameters",
    "ModelLocation",
    "ModelMessage",
    "ModelRegistry",
    "ModelRole",
    "ModelRoute",
    "ModelRuntime",
    "ModelTiming",
    "ModelToolCallRequest",
    "ModelToolDefinition",
    "RoutingRequirements",
    "StructuredResponseExpectation",
    "StructuredResponseKind",
]
