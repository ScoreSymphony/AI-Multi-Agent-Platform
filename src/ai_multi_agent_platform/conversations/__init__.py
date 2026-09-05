"""Canonical durable conversation interaction shell (issue #72)."""

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
from .service import ConversationService, TaskCreator

__all__ = [
    "AgentSelectionRef",
    "ContentKind",
    "Conversation",
    "ConversationContentBlock",
    "ConversationMessage",
    "ConversationNotFoundError",
    "ConversationParticipant",
    "ConversationRepository",
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
    "JsonConversationRepository",
    "MessageRole",
    "MessageStatus",
    "ModelRoutingPreference",
    "ParticipantKind",
    "RESERVED_CONVERSATION_METADATA_KEYS",
    "RETENTION_METADATA_KEY",
    "ReferenceKind",
    "ResourceReference",
    "TOMBSTONED_AT_METADATA_KEY",
    "TaskCreator",
]
