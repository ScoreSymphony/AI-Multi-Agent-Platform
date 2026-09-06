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
from .routing_profile_assignment import ModelRoutingProfileAssignmentGate
from .routing_profile_repository import (
    ROUTING_PROFILE_STORE_SCHEMA_VERSION,
    JsonModelRoutingProfileRepository,
    ModelRoutingProfileRepository,
)
from .routing_profile_resolution import ModelRoutingProfileResolver
from .routing_profile_service import ModelRoutingProfileService
from .routing_profiles import (
    MODEL_ROUTING_PROFILE_SCHEMA_VERSION,
    ModelRoutingProfileDefinition,
    ModelRoutingProfilePolicy,
    ModelRoutingProfileRef,
    ModelRoutingProfileRevision,
    RoutingProfileFallbackPolicy,
    new_model_routing_profile_id,
)
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
    "MODEL_ROUTING_PROFILE_SCHEMA_VERSION",
    "ROUTING_PROFILE_STORE_SCHEMA_VERSION",
    "CanonicalModelRequest",
    "CanonicalModelResponse",
    "DeterministicModelRouter",
    "JsonModelRegistryStore",
    "JsonModelRoutingProfileRepository",
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
    "ModelRoutingProfileAssignmentGate",
    "ModelRoutingProfileDefinition",
    "ModelRoutingProfilePolicy",
    "ModelRoutingProfileRef",
    "ModelRoutingProfileRepository",
    "ModelRoutingProfileResolver",
    "ModelRoutingProfileRevision",
    "ModelRoutingProfileService",
    "ModelRuntime",
    "ModelTiming",
    "ModelToolCallRequest",
    "ModelToolDefinition",
    "RoutingProfileFallbackPolicy",
    "RoutingRequirements",
    "StructuredResponseExpectation",
    "StructuredResponseKind",
    "new_model_routing_profile_id",
]
