from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from ai_multi_agent_platform.agents import AgentInstructions, AgentProfile, InstructionSource
from ai_multi_agent_platform.contracts import JsonValue, OperationContext
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.domain import OwnerRef, new_id
from ai_multi_agent_platform.evaluation import (
    EvaluationOutcome,
    EvaluationRunStatus,
    EvaluationService,
)
from ai_multi_agent_platform.learning import (
    LearningCandidate,
    LearningCandidateStatus,
    LearningGatePlan,
    LearningReference,
    LearningSourceType,
    LearningTarget,
    LearningTargetType,
    SQLiteLearningRepository,
)
from ai_multi_agent_platform.learning.scoped_control_plane import (
    LearningScopeAccess,
    ScopedLearningCommand,
)
from ai_multi_agent_platform.security import (
    ActorIdentity,
    ActorType,
    AuthorizationAction,
    LocalPrincipalPolicy,
    ResourceType,
    RiskClassification,
)

_EVALUATION_RUN_ID = "evaluation-run-scope-security"


def test_learning_evidence_authorizes_each_evaluation_run_id(tmp_path: Path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "evaluation-evidence-scope", secure_cookie=False)
        )
        project = deployment.scopes.create_project(
            key="evaluation-evidence-scope",
            name="Evaluation evidence scope",
            owner_type="user",
            owner_id="owner-a",
        )
        candidate, _ = deployment.learning.service.create_candidate(
            source_type=LearningSourceType.OPERATOR_PROPOSAL,
            problem="authorize exact Evaluation evidence",
            target=LearningTarget(
                resource_type=LearningTargetType.DOCUMENTATION,
                resource_id="evaluation-evidence-documentation",
                revision=1,
            ),
            improvement_type="documentation",
            expected_benefit="preserve resource-id authorization ceilings",
            risk=RiskClassification.STANDARD,
            gate_plan=_gate_plan(),
            creator_ref="user:evaluation-evidence",
            source_refs=(
                LearningReference(kind="scope-test", resource_id="evaluation-evidence-source"),
            ),
            project_id=project.id,
        )
        evaluation = _EvaluationLookup()
        evaluation.add(_EVALUATION_RUN_ID)
        deployment.learning.service.quality_gate.evaluation = cast(EvaluationService, evaluation)
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
            request_id="evaluation-evidence-command",
            correlation_id="evaluation-evidence-command",
            actor=ActorContext(principal_ref="user:evaluation-evidence-reader"),
        )

        result = await command(
            context,
            candidate.learning_candidate_id,
            {"evaluation_run_ids": [_EVALUATION_RUN_ID]},
        )

        assert result == {"authorized": True}
        assert access.calls == [
            ("learning.evidence", candidate.learning_candidate_id, project.id),
            ("learning.evidence", _EVALUATION_RUN_ID, project.id),
        ]

    asyncio.run(scenario())


def test_evaluation_regression_candidate_inherits_target_project_and_promotes(
    tmp_path: Path,
) -> None:
    deployment = build_single_node_deployment(
        SingleNodeConfig(data_dir=tmp_path / "evaluation-derived-scope", secure_cookie=False)
    )
    project = deployment.scopes.create_project(
        key="evaluation-derived-scope",
        name="Evaluation derived scope",
        owner_type="user",
        owner_id="owner-a",
    )
    agent = deployment.agents.create_agent(
        AgentProfile(
            name="Evaluation regression target",
            role="worker",
            description="Original behavior",
            instructions=AgentInstructions(
                role=InstructionSource(content="Use the original behavior.")
            ),
        ),
        owner_ref=OwnerRef(type="user", id="owner-a"),
        project_id=project.id,
    )
    evaluation = _EvaluationLookup()
    evaluation.add("evaluation-regression-source", regressions=True)
    evaluation.add("evaluation-regression-gate")
    deployment.learning.service.quality_gate.evaluation = cast(EvaluationService, evaluation)

    candidate, created = deployment.learning.service.create_from_evaluation_regression(
        "evaluation-regression-source",
        problem="The target revision regressed.",
        target=LearningTarget(
            resource_type=LearningTargetType.AGENT,
            resource_id=agent.agent_id,
            revision=agent.revision,
        ),
        improvement_type="agent_profile",
        expected_benefit="Restore evaluated behavior.",
        risk=RiskClassification.STANDARD,
        gate_plan=_gate_plan(),
        creator_ref="user:evaluation-derived",
        proposed_change={"description": "Restored evaluated behavior"},
    )

    assert created is True
    assert candidate.project_id == project.id

    evaluating = deployment.learning.service.record_gate_evidence(
        candidate.learning_candidate_id,
        evaluation_run_ids=("evaluation-regression-gate",),
        expected_revision=candidate.revision,
    )
    accepted = deployment.learning.service.accept(
        candidate.learning_candidate_id,
        expected_revision=evaluating.revision,
    )
    assert accepted.status is LearningCandidateStatus.ACCEPTED

    principal = "user:evaluation-derived-promoter"
    deployment.authorization.register(
        LocalPrincipalPolicy(
            principal_ref=principal,
            actor_types=frozenset({ActorType.HUMAN}),
            allowed_actions=frozenset({AuthorizationAction.MODIFY}),
            resource_types=frozenset({ResourceType.AGENT}),
            project_ids=frozenset({project.id}),
        )
    )
    promoted = asyncio.run(
        deployment.learning.service.promote(
            accepted.learning_candidate_id,
            actor=ActorIdentity(principal, ActorType.HUMAN),
            operation=OperationContext(
                correlation_id="evaluation-derived-promotion",
                project_id=project.id,
            ),
            expected_revision=accepted.revision,
        )
    )

    assert promoted.status is LearningCandidateStatus.PROMOTED
    latest = deployment.agents.get_agent_revision(agent.agent_id)
    assert latest.revision == agent.revision + 1
    assert latest.profile.description == "Restored evaluated behavior"


def test_sqlite_restart_migrates_legacy_candidate_dedupe_key(tmp_path: Path) -> None:
    database_path = tmp_path / "learning.sqlite3"
    repository = SQLiteLearningRepository(database_path)
    project_id = new_id("project")
    candidate = _candidate(project_id, "legacy-source")
    stored, created = repository.create_candidate(candidate, dedupe_key=candidate.dedupe_key)
    assert created is True

    legacy_key = _legacy_candidate_dedupe_key(candidate)
    assert legacy_key != candidate.dedupe_key
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            "UPDATE learning_candidate_keys SET dedupe_key = ? WHERE learning_candidate_id = ?",
            (legacy_key, candidate.learning_candidate_id),
        )

    restarted = SQLiteLearningRepository(database_path)
    migrated = restarted.find_candidate_by_dedupe_key(candidate.dedupe_key)
    assert migrated is not None
    assert migrated.learning_candidate_id == stored.learning_candidate_id

    duplicate = _candidate(project_id, "new-source-after-upgrade")
    deduped, duplicate_created = restarted.create_candidate(
        duplicate,
        dedupe_key=duplicate.dedupe_key,
    )
    assert duplicate_created is False
    assert deduped.learning_candidate_id == candidate.learning_candidate_id
    assert len(restarted.list_candidates()) == 1

    second_restart = SQLiteLearningRepository(database_path)
    assert (
        second_restart.find_candidate_by_dedupe_key(candidate.dedupe_key).learning_candidate_id
        == candidate.learning_candidate_id
    )


class _EvaluationLookup:
    def __init__(self) -> None:
        self._details: dict[str, SimpleNamespace] = {}

    def add(self, run_id: str, *, regressions: bool = False) -> None:
        result = SimpleNamespace(
            result_id=f"{run_id}-result",
            outcome=EvaluationOutcome.PASSED,
        )
        findings = (
            (
                SimpleNamespace(
                    current_result_id=result.result_id,
                    case_id="evaluation-regression-case",
                ),
            )
            if regressions
            else ()
        )
        self._details[run_id] = SimpleNamespace(
            run=SimpleNamespace(
                run_id=run_id,
                suite_id="learning-suite",
                suite_version="1",
                status=EvaluationRunStatus.COMPLETED,
            ),
            results=(result,),
            comparison=SimpleNamespace(regressions=findings),
        )

    def get_run_detail(self, run_id: str) -> SimpleNamespace:
        return self._details[run_id]


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


def _gate_plan() -> LearningGatePlan:
    return LearningGatePlan(
        policy_id="final-review-regression",
        policy_version=1,
        evaluation_suite_refs=("learning-suite@1",),
    )


def _candidate(project_id: str, source_id: str) -> LearningCandidate:
    return LearningCandidate(
        source_type=LearningSourceType.OPERATOR_PROPOSAL,
        problem="restart-safe dedupe migration",
        target=LearningTarget(
            resource_type=LearningTargetType.DOCUMENTATION,
            resource_id="legacy-dedupe-documentation",
            revision=1,
        ),
        improvement_type="documentation",
        expected_benefit="preserve canonical candidate identity after upgrade",
        risk=RiskClassification.STANDARD,
        gate_plan=_gate_plan(),
        creator_ref="user:dedupe-migration",
        source_refs=(LearningReference(kind="scope-test", resource_id=source_id),),
        proposed_change={"section": "same-change"},
        project_id=project_id,
    )


def _legacy_candidate_dedupe_key(candidate: LearningCandidate) -> str:
    payload = {
        "source_type": candidate.source_type.value,
        "problem": candidate.problem,
        "target": {
            "resource_type": candidate.target.resource_type.value,
            "resource_id": candidate.target.resource_id,
            "revision": candidate.target.revision,
        },
        "improvement_type": candidate.improvement_type,
        "proposed_change": dict(candidate.proposed_change),
        "proposed_artifact_ref": None,
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
