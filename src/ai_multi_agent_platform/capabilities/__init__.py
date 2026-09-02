"""Canonical capability registry, invocation pipeline and tool adapters."""

from .invocation import (
    ApprovalHook,
    CapabilityInvoker,
    InvocationObserver,
    NullInvocationObserver,
    PolicyHook,
)
from .mcp import MCPClient, MCPServerConfig, MCPTool, MCPToolProvider
from .native import ECHO_CAPABILITY_ID, NativeEchoProvider
from .provider import CapabilityToolProvider
from .registry import CapabilityRegistry
from .types import (
    CapabilityInvocation,
    CapabilityInvocationResult,
    CapabilityRegistration,
    CapabilitySpec,
    InvocationRecord,
    InvocationStatus,
    InvocationTrace,
    PolicyDecision,
    SafetyClassification,
    SideEffectClassification,
)

__all__ = [
    "ApprovalHook",
    "CapabilityInvocation",
    "CapabilityInvocationResult",
    "CapabilityInvoker",
    "CapabilityRegistration",
    "CapabilityRegistry",
    "CapabilitySpec",
    "CapabilityToolProvider",
    "ECHO_CAPABILITY_ID",
    "InvocationObserver",
    "InvocationRecord",
    "InvocationStatus",
    "InvocationTrace",
    "MCPClient",
    "MCPServerConfig",
    "MCPTool",
    "MCPToolProvider",
    "NativeEchoProvider",
    "NullInvocationObserver",
    "PolicyDecision",
    "PolicyHook",
    "SafetyClassification",
    "SideEffectClassification",
]
