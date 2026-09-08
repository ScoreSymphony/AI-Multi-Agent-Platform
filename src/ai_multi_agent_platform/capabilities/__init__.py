"""Canonical capability registry and invocation pipeline."""

from .canonical_binding import (
    bind_canonical_capability_invocation,
    canonical_tool_id,
    canonical_tool_invocation_id,
)
from .egress import CapabilityClassificationResolver, EgressCapabilityInvoker
from .invocation import (
    ApprovalHook,
    CanonicalInvocationBindingHook,
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
    ISOLATED_WORKSPACE_WRITE_FEATURE,
    CapabilityCompatibilityRequest,
    CapabilityDiscoveryRequest,
    CapabilityInvocation,
    CapabilityInvocationResult,
    CapabilityRegistration,
    CapabilitySpec,
    CompensationDescriptor,
    CompensationIdempotency,
    CredentialRequirement,
    InvocationRecord,
    InvocationStatus,
    InvocationTrace,
    PolicyDecision,
    ReversibilityClassification,
    SafetyClassification,
    SideEffectClassification,
)

# Keep the established public name while making egress enforcement the default application path.
CapabilityInvoker = EgressCapabilityInvoker

__all__ = [
    "ApprovalHook",
    "CanonicalInvocationBindingHook",
    "CapabilityClassificationResolver",
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
    "CompensationDescriptor",
    "CompensationIdempotency",
    "CredentialRequirement",
    "ECHO_CAPABILITY_ID",
    "EgressCapabilityInvoker",
    "EventRepositoryInvocationObserver",
    "GovernanceBindingHook",
    "ISOLATED_WORKSPACE_WRITE_FEATURE",
    "InvocationObserver",
    "InvocationRecord",
    "InvocationStatus",
    "InvocationTrace",
    "NativeEchoProvider",
    "NullInvocationObserver",
    "PolicyDecision",
    "PolicyHook",
    "ReversibilityClassification",
    "SafetyClassification",
    "SideEffectClassification",
    "bind_canonical_capability_invocation",
    "canonical_tool_id",
    "canonical_tool_invocation_id",
]
