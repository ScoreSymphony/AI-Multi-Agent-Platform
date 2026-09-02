"""Canonical capability registry and invocation pipeline."""

from .invocation import (
    ApprovalHook,
    CapabilityInvoker,
    GovernanceBindingHook,
    InvocationObserver,
    NullInvocationObserver,
    PolicyHook,
)
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
    "GovernanceBindingHook",
    "InvocationObserver",
    "InvocationRecord",
    "InvocationStatus",
    "InvocationTrace",
    "NativeEchoProvider",
    "NullInvocationObserver",
    "PolicyDecision",
    "PolicyHook",
    "SafetyClassification",
    "SideEffectClassification",
]
