"""Terminal and execution-session foundation."""

from .contracts import (
    AdapterFrame,
    AdapterSessionHandle,
    AdapterStartResult,
    SessionCreateRequest,
    TerminalAdapterDescriptor,
    TerminalSessionAdapter,
)
from .models import (
    TERMINAL_SESSION_STATUSES,
    AttachmentStatus,
    SessionAttachment,
    SessionContext,
    SessionMode,
    SessionStatus,
    SessionType,
    StreamChannel,
    TerminalCapabilities,
    TerminalDimensions,
    TerminalFrame,
    TerminalSession,
)
from .reference import ReferenceTerminalAdapter
from .service import TerminalActivityRecord, TerminalSessionService

__all__ = [
    "TERMINAL_SESSION_STATUSES",
    "AdapterFrame",
    "AdapterSessionHandle",
    "AdapterStartResult",
    "AttachmentStatus",
    "ReferenceTerminalAdapter",
    "SessionAttachment",
    "SessionContext",
    "SessionCreateRequest",
    "SessionMode",
    "SessionStatus",
    "SessionType",
    "StreamChannel",
    "TerminalActivityRecord",
    "TerminalAdapterDescriptor",
    "TerminalCapabilities",
    "TerminalDimensions",
    "TerminalFrame",
    "TerminalSession",
    "TerminalSessionAdapter",
    "TerminalSessionService",
]
