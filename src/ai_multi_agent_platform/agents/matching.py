"""Stable public facade for provider-neutral Agent and AgentTeam matching."""

from .matching_contracts import (
    AgentCandidateKind,
    AgentCapabilityRequirement,
    AgentMatchCandidate,
    AgentMatchCandidateOutcome,
    AgentMatchingPolicy,
    AgentMatchingRequirements,
    AgentMatchReason,
    AgentMatchRejection,
    AgentMatchResult,
    AgentMatchStatus,
    AgentParticipantRef,
    AgentTieBreakPolicy,
    CallableAgentMatchingPolicy,
)
from .matching_engine import AgentMatcher
from .matching_resolution import AgentResolver

__all__ = [
    "AgentCandidateKind",
    "AgentCapabilityRequirement",
    "AgentMatchCandidate",
    "AgentMatchCandidateOutcome",
    "AgentMatchReason",
    "AgentMatchRejection",
    "AgentMatchResult",
    "AgentMatchStatus",
    "AgentMatcher",
    "AgentMatchingPolicy",
    "AgentMatchingRequirements",
    "AgentParticipantRef",
    "AgentResolver",
    "AgentTieBreakPolicy",
    "CallableAgentMatchingPolicy",
]
