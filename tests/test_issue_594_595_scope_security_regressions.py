from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, JsonValue
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.learning.models import (
    LearningGatePlan,
    LearningReference,
    LearningSourceType,
    LearningTarget,
    LearningTargetType,
)
from ai_multi_agent_platform.learning.scoped_control_plane import (
    LearningScopeAccess,
    ScopedLearningCommand,
)
from ai_multi_agent_platform.security import RiskClassification
from ai_multi_agent_platform.verification import VerificationService

_VERIFICATION_ID = "verification_00000000-0000-4000-8000-000000000595"


def test_candidate_dedupe_is_project_scoped(tmp_path: Path) -> None:
    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "dedupe-scope", secure_cookie=False)
    )
    project_a = deployment.scopes.create_project(
        key="dedupe-scope-a",
        name="Dedupe scope A",
        owner_type="user",
        owner_id="owner-a",
    )
    project_b = deployment.scopes.create_project(
        key="dedupe-scope-b",
        name="Dedupe scope B",
        owner_type="user",
        owner_id="owner-b",
    )

    candidate_b, created_b = _create_documentation_candidate(
        deployment,
        project_b.id,
        source_id="project-b-source",
    )
    candidate_a, created_a = _create_documentation_candidate(
        deployment,
        project_a.id,
        source_id="project-a-source",
    )

    assert created_b is True
    assert created_a is True
    assert candidate_a.learning_candidate_id != candidate_b.learning_candidate_id
    assert candidate_a.project_id == project_a.id
    assert candidate_b.project_id == project_b.id

    linked_a, created_again = _create_documentation_candidate(
        deployment,
        project_a.id,
        source_id="project-a-second-source",
    )

    assert created_again is False
    assert linked_a.learning_candidate_id == candidate_a.learning_candidate_id
    assert linked_a.project_id == project_a.id
    assert linked_a.revision == 2
    assert deployment.learning.service.get_candidate(candidate_b.learning_candidate_id).revision == 1
    assert {reference.resource_id for reference in linked_a.source_refs} == {
        "project-a-source",
        "project-a-second-source",
    }


def test_learning_evidence_rejects_cross_project_verification_in_service(tmp_path: Path) -> None:
    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "evidence-domain-scope", secure_cookie=False)
    )
    project_a = deployment.scopes.create_project(
        key="evidence-domain-a",
        name="Evidence domain A",
        owner_type="user",
        owner_id="owner-a",
    )
    project_b = deployment.scopes.create_project(
        key="evidence-domain-b",
        name="Evidence domain B",
        owner_type="user",
        owner_id="owner-b",
    )
    candidate, _ = _create_documentation_candidate(
        deployment,
        project_a.id,
        source_id="domain-evidence-source",
    )
    lookup = _VerificationLookup(project_b.id)
    deployment.learning.service.quality_gate.verification = cast(VerificationService, lookup)

    with pytest.raises(ContractError) as denied:
        deployment.learning.service.record_gate_evidence(
            candidate.learning_candidate_id,
            verification_ids=(_VERIFICATION_ID,),
            expected_revision=candidate.revision,
        )

    assert denied.value.code is ErrorCode.FORBIDDEN
    assert deployment.learning.service.get_candidate(candidate.learning_candidate_id).revision == 1


def test_learning_evidence_authorizes_each_verification_id_and_project(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "evidence-command-scope", secure_cookie=False)
        )
        project = deployment.scopes.create_project(
            key="evidence-command",
            name="Evidence command",
            owner_type="user",
            owner_id="owner-a",
        )
        candidate, _ = _create_documentation_candidate(
            deployment,
            project.id,
            source_id="command-evidence-source",
        )
        lookup = _VerificationLookup(project.id)
        deployment.learning.service.quality_gate.verification = cast(VerificationService, lookup)
        access = _RecordingLearningAccess()

        async def delegate(
            context: RequestContext,
            resource_ref: str,
            payload: dict[str, JsonValue],
        ) -> dict[str, JsonValue]:
            del context, resource_ref, payload
            return {"authorized": True}

        command = ScopedLearningCommand(
            "learning.evidence",
            delegate,
            deployment.learning.service,
            cast(LearningScopeAccess, access),
        )
        context = RequestContext(
            request_id="evidence-command",
            correlation_id="evidence-command",
            actor=ActorContext(principal_ref="user:evidence-reader"),
        )

        result = await command(
            context,
            candidate.learning_candidate_id,
            {"verification_ids": [_VERIFICATION_ID]},
        )

        assert result == {"authorized": True}
        assert access.calls == [
            (
                "learning.evidence",
                candidate.learning_candidate_id,
                project.id,
            ),
            ("learning.evidence", _VERIFICATION_ID, project.id),
        ]

    asyncio.run(scenario())


class _VerificationLookup:
    def __init__(self, project_id: str) -> None:
        self.project_id = project_id

    def get_request(self, verification_id: str) -> SimpleNamespace:
        assert verification_id == _VERIFICATION_ID
        return SimpleNamespace(
            verification_id=verification_id,
            policy_id="verification-policy-test",
            policy_version=1,
            project_id=self.project_id,
        )

    def result_for(self, verification_id: str) -> None:
        assert verification_id == _VERIFICATION_ID
        return None


class _RecordingLearningAccess:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str | None]] = []

    async def authorize(
        self,
        context: RequestContext,
        action: str,
        resource_ref: str,
        *,
        project_id: str | None,
    ) -> None:
        del context
        self.calls.append((action, resource_ref, project_id))


def _create_documentation_candidate(
    deployment: object,
    project_id: str,
    *,
    source_id: str,
):
    service = getattr(getattr(deployment, "learning"), "service")
    return service.create_candidate(
        source_type=LearningSourceType.OPERATOR_PROPOSAL,
        problem="project-scoped dedupe regression",
        target=LearningTarget(
            resource_type=LearningTargetType.DOCUMENTATION,
            resource_id="documentation-shared-target",
            revision=1,
        ),
        improvement_type="documentation",
        expected_benefit="preserve project isolation",
        risk=RiskClassification.STANDARD,
        gate_plan=LearningGatePlan(
            policy_id="scope-security-regression",
            policy_version=1,
            evaluation_suite_refs=("single-node.reference.lifecycle@1.0",),
        ),
        creator_ref="user:scope-security-test",
        source_refs=(LearningReference(kind="scope-test", resource_id=source_id),),
        proposed_change={"section": "same-change"},
        project_id=project_id,
    )
