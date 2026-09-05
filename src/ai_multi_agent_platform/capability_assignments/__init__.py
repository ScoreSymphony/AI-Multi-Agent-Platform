"""Canonical reusable capability-assignment policy resources."""

from .models import (
    CAPABILITY_ASSIGNMENT_SCHEMA_VERSION,
    CapabilityAssignmentContent,
    CapabilityAssignmentPolicy,
    CapabilityAssignmentProvenance,
    CapabilityAssignmentRevision,
    CapabilityAssignmentRule,
    CapabilityAssignmentTarget,
    CapabilityAssignmentTargetType,
)
from .persistence import JsonCapabilityAssignmentRepository
from .repository import (
    CapabilityAssignmentRepository,
    InMemoryCapabilityAssignmentRepository,
)
from .service import (
    CallableCapabilityAssignmentTargetResolver,
    CapabilityAssignmentAccessContext,
    CapabilityAssignmentAuthorizationGate,
    CapabilityAssignmentService,
    CapabilityAssignmentTargetResolver,
    CapabilityInventory,
    ResolvedCapabilityAssignmentTarget,
)

__all__ = [
    "CAPABILITY_ASSIGNMENT_SCHEMA_VERSION",
    "CallableCapabilityAssignmentTargetResolver",
    "CapabilityAssignmentAccessContext",
    "CapabilityAssignmentAuthorizationGate",
    "CapabilityAssignmentContent",
    "CapabilityAssignmentPolicy",
    "CapabilityAssignmentProvenance",
    "CapabilityAssignmentRepository",
    "CapabilityAssignmentRevision",
    "CapabilityAssignmentRule",
    "CapabilityAssignmentService",
    "CapabilityAssignmentTarget",
    "CapabilityAssignmentTargetResolver",
    "CapabilityAssignmentTargetType",
    "CapabilityInventory",
    "InMemoryCapabilityAssignmentRepository",
    "JsonCapabilityAssignmentRepository",
    "ResolvedCapabilityAssignmentTarget",
]
