"""Canonical capability registry and invocation pipeline."""

from .canonical_binding import (
    bind_canonical_capability_invocation,
    canonical_tool_id,
    canonical_tool_invocation_id,
)
from .invocation import (
    ApprovalHook,
    CanonicalInvocationBindingHook,
    CapabilityInvoker,
    GovernanceBindingHook,
    InvocationObserver,
    NullInvocationObserver,
    PolicyHook,
)
from .native import ECHO_CAPABILITY_ID, NativeEchoProvider
from .observer import EventRepositoryInvocationObserver
from .provider import CapabilityToolProvider
from .registry import CapabilityDiscoveryPolicyHook, CapabilityRegistry
from .types import (
    CapabilityCompatibilityRequest,
    CapabilityDiscoveryRequest,
    CapabilityInvocation,
    CapabilityInvocationResult,
    CapabilityRegistration,
    CapabilitySpec,
    CredentialRequirement,
    InvocationRecord,
    InvocationStatus,
    InvocationTrace,
    PolicyDecision,
    SafetyClassification,
    SideEffectClassification,
)

__all__ = [
    "ApprovalHook",
    "CanonicalInvocationBindingHook",
    "CapabilityCompatibilityRequest",
    "CapabilityDiscoveryPolicyHook",
    "CapabilityDiscoveryRequest",
    "CapabilityInvocation",
    "CapabilityInvocationResult",
    "CapabilityInvoker",
    "CapabilityRegistration",
    "CapabilityRegistry",
    "CapabilitySpec",
    "CapabilityToolProvider",
    "CredentialRequirement",
    "ECHO_CAPABILITY_ID",
    "EventRepositoryInvocationObserver",
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
    "bind_canonical_capability_invocation",
    "canonical_tool_id",
    "canonical_tool_invocation_id",
]
