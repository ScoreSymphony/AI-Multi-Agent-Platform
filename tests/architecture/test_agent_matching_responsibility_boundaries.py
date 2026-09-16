"""Pin stable Agent-matching facade identity to focused implementation owners."""

from ai_multi_agent_platform.agents import (
    AgentMatchCandidate,
    AgentMatcher,
    AgentMatchingRequirements,
    AgentResolver,
    matching,
)
from ai_multi_agent_platform.agents.matching_contracts import (
    AgentMatchCandidate as ContractAgentMatchCandidate,
)
from ai_multi_agent_platform.agents.matching_contracts import (
    AgentMatchingRequirements as ContractAgentMatchingRequirements,
)
from ai_multi_agent_platform.agents.matching_engine import AgentMatcher as EngineAgentMatcher
from ai_multi_agent_platform.agents.matching_resolution import (
    AgentResolver as RepositoryAgentResolver,
)


def test_matching_facade_preserves_public_symbol_identity() -> None:
    assert matching.AgentMatchCandidate is ContractAgentMatchCandidate
    assert matching.AgentMatchingRequirements is ContractAgentMatchingRequirements
    assert matching.AgentMatcher is EngineAgentMatcher
    assert matching.AgentResolver is RepositoryAgentResolver

    assert AgentMatchCandidate is ContractAgentMatchCandidate
    assert AgentMatchingRequirements is ContractAgentMatchingRequirements
    assert AgentMatcher is EngineAgentMatcher
    assert AgentResolver is RepositoryAgentResolver


def test_matching_responsibilities_have_focused_module_owners() -> None:
    assert AgentMatchCandidate.__module__.endswith(".matching_contracts")
    assert AgentMatchingRequirements.__module__.endswith(".matching_contracts")
    assert AgentMatcher.__module__.endswith(".matching_engine")
    assert AgentResolver.__module__.endswith(".matching_resolution")
