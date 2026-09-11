from __future__ import annotations

import pytest

from ai_multi_agent_platform.agents import STANDARD_AGENT_IDS, STANDARD_TEAM_IDS
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.verification import (
    VerificationPolicy,
    VerificationStage,
    VerifierKind,
)
from ai_multi_agent_platform.verification.agent_workflow import ReviewerAssignment
from ai_multi_agent_platform.verification.output_workflow import _automatic_review_configuration
from ai_multi_agent_platform.verification.reviewer_routing import ReviewerDiscoverySelector


def _policy(stage_config: dict[str, object]) -> VerificationPolicy:
    return VerificationPolicy(
        name="issue-759-reviewer-routing",
        stages=(VerificationStage(stage_id="review", verifier_kind=VerifierKind.AGENT),),
        metadata={
            "automatic_reviewer": {
                "enabled": True,
                "subject_types": ["result", "artifact"],
                "stages": {"review": stage_config},
            }
        },
    )


def test_exact_assignment_metadata_remains_backward_compatible() -> None:
    configuration = _automatic_review_configuration(
        _policy(
            {
                "team_id": STANDARD_TEAM_IDS["software_development"],
                "team_revision": 1,
                "team_role": "reviewer_tester",
            }
        ),
        required=True,
    )

    assert configuration is not None
    route = configuration.routes["review"]
    assert isinstance(route, ReviewerAssignment)
    assert route.team_role == "reviewer_tester"


def test_policy_metadata_accepts_explicitly_scoped_role_capability_discovery() -> None:
    configuration = _automatic_review_configuration(
        _policy(
            {
                "candidate_agent_ids": [
                    STANDARD_AGENT_IDS["developer"],
                    STANDARD_AGENT_IDS["reviewer"],
                ],
                "candidate_team_ids": [STANDARD_TEAM_IDS["software_development"]],
                "reviewer_role": "reviewer",
                "required_capability_ids": ["tool.file.read"],
            }
        ),
        required=True,
    )

    assert configuration is not None
    route = configuration.routes["review"]
    assert isinstance(route, ReviewerDiscoverySelector)
    assert route.reviewer_role == "reviewer"
    assert route.required_capability_ids == ("tool.file.read",)


def test_policy_metadata_rejects_mixing_exact_and_discovery_routes() -> None:
    with pytest.raises(ContractError) as caught:
        _automatic_review_configuration(
            _policy(
                {
                    "agent_id": STANDARD_AGENT_IDS["reviewer"],
                    "agent_revision": 1,
                    "candidate_agent_ids": [STANDARD_AGENT_IDS["reviewer"]],
                    "reviewer_role": "reviewer",
                }
            ),
            required=True,
        )

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION


def test_policy_metadata_rejects_unbounded_discovery() -> None:
    with pytest.raises(ContractError) as caught:
        _automatic_review_configuration(
            _policy({"reviewer_role": "reviewer"}),
            required=True,
        )

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION


def test_policy_metadata_rejects_discovery_scope_without_semantic_selector() -> None:
    with pytest.raises(ContractError) as caught:
        _automatic_review_configuration(
            _policy({"candidate_agent_ids": [STANDARD_AGENT_IDS["reviewer"]]}),
            required=True,
        )

    assert caught.value.code is ErrorCode.INVALID_CONFIGURATION
