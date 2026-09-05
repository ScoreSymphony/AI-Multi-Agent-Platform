"""Canonical durable conversation interaction shell (issue #72)."""

from .context_resolution import (
    ContextResolvingConversationResponseProvider,
    resolve_conversation_context,
)
from .model_runtime_response import (
    ConversationInstructionResolver,
    ModelRuntimeConversationResponseProvider,
)
from .models import (
    AgentSelectionRef,
    ContentKind,
    Conversation,
    ConversationContentBlock,
    ConversationMessage,
    ConversationParticipant,
    ConversationStatus,
    MessageRole,
    MessageStatus,
    ModelRoutingPreference,
    ParticipantKind,
    ReferenceKind,
    ResourceReference,
)
from .repository import (
    ConversationNotFoundError,
    ConversationRepository,
    JsonConversationRepository,
)
from .responses import (
    ConversationResolvedContext,
    ConversationResponseChunk,
    ConversationResponseChunkKind,
    ConversationResponseProvider,
    ConversationResponseRequest,
    ConversationResponseTarget,
)
from .retention import (
    RESERVED_CONVERSATION_METADATA_KEYS,
    RETENTION_METADATA_KEY,
    TOMBSTONED_AT_METADATA_KEY,
    ConversationRetentionManager,
    ConversationRetentionMode,
    ConversationRetentionPolicy,
)
from .routing_profile_response import DurableRoutingProfileConversationResponseProvider
from .service import ConversationService, TaskCreator

__all__ = [
    "AgentSelectionRef",
    "ContentKind",
    "ContextResolvingConversationResponseProvider",
    "Conversation",
    "ConversationContentBlock",
    "ConversationInstructionResolver",
    "ConversationMessage",
    "ConversationNotFoundError",
    "ConversationParticipant",
    "ConversationRepository",
    "ConversationResolvedContext",
    "ConversationResponseChunk",
    "ConversationResponseChunkKind",
    "ConversationResponseProvider",
    "ConversationResponseRequest",
    "ConversationResponseTarget",
    "ConversationRetentionManager",
    "ConversationRetentionMode",
    "ConversationRetentionPolicy",
    "ConversationService",
    "ConversationStatus",
    "DurableRoutingProfileConversationResponseProvider",
    "JsonConversationRepository",
    "MessageRole",
    "MessageStatus",
    "ModelRoutingPreference",
    "ModelRuntimeConversationResponseProvider",
    "ParticipantKind",
    "RESERVED_CONVERSATION_METADATA_KEYS",
    "RETENTION_METADATA_KEY",
    "ReferenceKind",
    "ResourceReference",
    "TOMBSTONED_AT_METADATA_KEY",
    "TaskCreator",
    "resolve_conversation_context",
]
