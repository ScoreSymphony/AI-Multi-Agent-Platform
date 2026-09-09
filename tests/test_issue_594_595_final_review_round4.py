from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane import ActorContext, RequestContext
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.evaluation.models import (
    ConfigurationSnapshot,
    EvaluationCase,
    EvaluationSuite,
)
from ai_multi_agent_platform.evaluation.routing_profile_snapshot import (
    RoutingProfileAwareEvaluationTargetSnapshotEnricher,
)
from ai_multi_agent_platform.models import new_model_routing_profile_id
from ai_multi_agent_platform.security import (
    ActorType,
    AuthorizationAction,
    LocalPrincipalPolicy,
    ResourceType,
    canonical_control_plane_vocabulary,
)


def test_eval_snapshot_keeps_distinct_revisions_of_shared_routing_profile() -> None:
    profile_id = new_model_routing_profile_id()
    first_agent_id = "agent_00000000-0000-4000-8000-000000000601"
    second_agent_id = "agent_00000000-0000-4000-8000-000000000602"
    revisions = {
        first_agent_id: SimpleNamespace(
            profile=SimpleNamespace(
                model=SimpleNamespace(routing_profile_ref=f"{profile_id}@r1")
            )
        ),
        second_agent_id: SimpleNamespace(
            profile=SimpleNamespace(
                model=SimpleNamespace(routing_profile_ref=f"{profile_id}@r2")
            )
        ),
    }
    agents = SimpleNamespace(
        service=SimpleNamespace(
            get_agent_revision=lambda agent_id, _revision: revisions[agent_id]
        )
    )
    enricher = RoutingProfileAwareEvaluationTargetSnapshotEnricher(
        agents=agents,
        models=SimpleNamespace(),
    )
    enricher._base = SimpleNamespace(enrich=lambda _suite, snapshot: snapshot)
    suite = EvaluationSuite(
        suite_id="integration.routing-profile.shared-revisions",
        name="Shared routing profile revisions",
        version="1",
        cases=(
            _agent_case("shared-routing-profile.first", first_agent_id, 3),
            _agent_case("shared-routing-profile.second", second_agent_id, 4),
        ),
    )

    enriched = enricher.enrich(suite, ConfigurationSnapshot(platform_version="test"))

    routing_refs = {
        (reference.ref_id, reference.version)
        for reference in enriched.references
        if reference.kind == "model_routing_profile"
    }
    assert routing_refs == {
        (f"{profile_id}@r1", "1"),
        (f"{profile_id}@r2", "2"),
    }


def test_learning_create_commands_use_create_authorization_vocabulary() -> None:
    for action in (
        "learning.feedback.create",
        "learning.propose",
        "learning.propose-from-feedback",
    ):
        assert canonical_control_plane_vocabulary(action) == (
            AuthorizationAction.CREATE,
            ResourceType.GENERIC,
        )
    assert canonical_control_plane_vocabulary("learning.evidence") == (
        AuthorizationAction.MODIFY,
        ResourceType.GENERIC,
    )


def test_learning_feedback_creation_requires_create_not_modify(tmp_path) -> None:
    async def scenario() -> None:
        deployment = build_single_node_deployment(
            SingleNodeConfig(data_dir=tmp_path / "learning-create-auth", secure_cookie=False)
        )
        project = deployment.scopes.create_project(
            key="learning-create-auth",
            name="Learning create authorization",
            owner_type="user",
            owner_id="owner-a",
        )
        creator = "user:learning-creator"
        modifier = "user:learning-modifier"
        deployment.authorization.register(
            LocalPrincipalPolicy(
                principal_ref=creator,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.CREATE}),
                resource_types=frozenset({ResourceType.GENERIC}),
                project_ids=frozenset({project.id}),
            )
        )
        deployment.authorization.register(
            LocalPrincipalPolicy(
                principal_ref=modifier,
                actor_types=frozenset({ActorType.HUMAN}),
                allowed_actions=frozenset({AuthorizationAction.MODIFY}),
                resource_types=frozenset({ResourceType.GENERIC}),
                project_ids=frozenset({project.id}),
            )
        )

        created = await deployment.control_plane.execute_command(
            _command_context("create-allowed", creator),
            "learning.feedback.create",
            "learning-feedback",
            _feedback_payload(project.id, "creator"),
        )
        assert created["project_id"] == project.id

        with pytest.raises(ContractError) as denied:
            await deployment.control_plane.execute_command(
                _command_context("create-denied", modifier),
                "learning.feedback.create",
                "learning-feedback",
                _feedback_payload(project.id, "modifier"),
            )
        assert denied.value.code is ErrorCode.FORBIDDEN

    asyncio.run(scenario())


def _agent_case(case_id: str, agent_id: str, revision: int) -> EvaluationCase:
    return EvaluationCase(
        case_id=case_id,
        name=case_id,
        version="1",
        input_template={
            "evaluation_target": {
                "kind": "agent",
                "agent_id": agent_id,
                "agent_revision": revision,
            }
        },
    )


def _command_context(request_id: str, principal_ref: str) -> RequestContext:
    return RequestContext(
        request_id=request_id,
        correlation_id=request_id,
        idempotency_key=request_id,
        actor=ActorContext(
            principal_ref=principal_ref,
            actor_type=ActorType.HUMAN.value,
        ),
    )


def _feedback_payload(project_id: str, suffix: str) -> dict[str, object]:
    return {
        "feedback_type": "comment",
        "subject": {"kind": "scope-test", "resource_id": f"create-{suffix}"},
        "comment": "authorization vocabulary regression",
        "project_id": project_id,
    }
