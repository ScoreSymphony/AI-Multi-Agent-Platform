from __future__ import annotations

import asyncio
from pathlib import Path

from ai_multi_agent_platform.agents import (
    AgentInstructions,
    AgentProfile,
    AgentService,
    InMemoryAgentRepository,
    InstructionSource,
)
from ai_multi_agent_platform.control_plane import ScopeStore
from ai_multi_agent_platform.domain import OwnerRef
from ai_multi_agent_platform.evaluation import (
    DeterministicAssertionEvaluator,
    EvaluationAttempt,
    EvaluationCase,
    EvaluationExecutionContext,
    EvaluationObservation,
    EvaluationRunner,
    EvaluationService,
    EvaluationSuite,
    InMemoryEvaluationRepository,
)
from ai_multi_agent_platform.evaluation.suite_assets import (
    SqliteEvaluationSuiteAssetRepository,
)
from ai_multi_agent_platform.models import ModelRegistry
from ai_multi_agent_platform.portability.composition import build_agent_portability_workflow
from ai_multi_agent_platform.portability.evaluation_codecs import EVALUATION_SUITE_RESOURCE_TYPE
from ai_multi_agent_platform.portability.models import IdPolicy
from ai_multi_agent_platform.portability.package import package_to_dict
from ai_multi_agent_platform.portability.workflow import ExportSelection


class StaticEvaluationExecutor:
    async def execute_case(
        self,
        *,
        case: EvaluationCase,
        attempt: EvaluationAttempt,
        execution_context: EvaluationExecutionContext,
    ) -> EvaluationObservation:
        del case, attempt, execution_context
        return EvaluationObservation()


def _evaluation_service(
    *,
    suites: tuple[EvaluationSuite, ...] = (),
    asset_path: Path | None = None,
) -> EvaluationService:
    repository = InMemoryEvaluationRepository()
    service = EvaluationService(
        repository=repository,
        runner=EvaluationRunner(
            repository=repository,
            executor=StaticEvaluationExecutor(),
            evaluators=(DeterministicAssertionEvaluator(),),
        ),
        suites=suites,
    )
    if asset_path is not None:
        service.attach_suite_assets(SqliteEvaluationSuiteAssetRepository(asset_path))
    return service


def _agent(service: AgentService) -> str:
    revision = service.create_agent(
        AgentProfile(
            name="Portable evaluation target",
            role="worker",
            instructions=AgentInstructions(
                role=InstructionSource(content="Return deterministic test output."),
            ),
        ),
        owner_ref=OwnerRef(type="service", id="portability-test"),
    )
    return revision.agent_id


def _suite(agent_id: str) -> EvaluationSuite:
    return EvaluationSuite(
        suite_id="portable.agent-suite",
        name="Portable Agent suite",
        version="1.2",
        cases=(
            EvaluationCase(
                case_id="portable.agent-suite.case",
                name="Portable Agent target",
                version="1.0",
                input_template={
                    "evaluation_target": {
                        "kind": "agent",
                        "agent_id": agent_id,
                        "agent_revision": 1,
                    }
                },
            ),
        ),
    )


def test_evaluation_suite_round_trip_uses_existing_portability_preview_and_remapping(
    tmp_path: Path,
) -> None:
    source_agents = InMemoryAgentRepository()
    source_agent_id = _agent(AgentService(source_agents))
    suite = _suite(source_agent_id)
    source_evaluation = _evaluation_service(suites=(suite,))
    source_workflow = build_agent_portability_workflow(
        agents=source_agents,
        models=ModelRegistry(),
        scopes=ScopeStore(),
        evaluation=source_evaluation,
        platform_version="0.0.1",
        id_policy=IdPolicy.REGENERATE,
    )

    exported = asyncio.run(
        source_workflow.export_package(
            (
                ExportSelection("agent", source_agent_id),
                ExportSelection(EVALUATION_SUITE_RESOURCE_TYPE, "portable.agent-suite@1.2"),
            )
        )
    )

    destination_agents = InMemoryAgentRepository()
    destination_path = tmp_path / "evaluation.sqlite3"
    destination_evaluation = _evaluation_service(asset_path=destination_path)
    destination_workflow = build_agent_portability_workflow(
        agents=destination_agents,
        models=ModelRegistry(),
        scopes=ScopeStore(),
        evaluation=destination_evaluation,
        platform_version="0.0.1",
        id_policy=IdPolicy.REGENERATE,
    )
    incoming = destination_workflow.validate_package_document(package_to_dict(exported.package))
    preview = destination_workflow.preview_import(incoming.package_id)

    assert preview.ready is True
    assert preview.preview.import_order == (
        ("agent", source_agent_id),
        (EVALUATION_SUITE_RESOURCE_TYPE, "portable.agent-suite@1.2"),
    )
    target_agent_id = preview.preview.mapping_dict()[("agent", source_agent_id)]
    assert target_agent_id != source_agent_id

    report = asyncio.run(destination_workflow.execute_import(preview.preview_id))
    assert tuple(item.resource_type for item in report.result.resources) == (
        "agent",
        EVALUATION_SUITE_RESOURCE_TYPE,
    )

    imported = destination_evaluation.get_suite("portable.agent-suite@1.2")
    target = imported.cases[0].input_template["evaluation_target"]
    assert isinstance(target, dict)
    assert target["agent_id"] == target_agent_id
    assert destination_agents.get_agent(target_agent_id).current_revision == 1

    restarted = _evaluation_service(asset_path=destination_path)
    restored = restarted.get_suite("portable.agent-suite@1.2")
    assert restored == imported


def test_evaluation_suite_existing_exact_version_conflicts_in_preview(tmp_path: Path) -> None:
    source_agents = InMemoryAgentRepository()
    source_agent_id = _agent(AgentService(source_agents))
    suite = _suite(source_agent_id)
    source_workflow = build_agent_portability_workflow(
        agents=source_agents,
        models=ModelRegistry(),
        scopes=ScopeStore(),
        evaluation=_evaluation_service(suites=(suite,)),
        platform_version="0.0.1",
    )
    exported = asyncio.run(
        source_workflow.export_package(
            [ExportSelection(EVALUATION_SUITE_RESOURCE_TYPE, "portable.agent-suite@1.2")]
        )
    )

    destination = _evaluation_service(asset_path=tmp_path / "evaluation.sqlite3")
    destination.create_suite(suite)
    workflow = build_agent_portability_workflow(
        agents=source_agents,
        models=ModelRegistry(),
        scopes=ScopeStore(),
        evaluation=destination,
        platform_version="0.0.1",
    )
    incoming = workflow.validate_package_document(package_to_dict(exported.package))
    preview = workflow.preview_import(incoming.package_id)

    assert preview.ready is False
    assert any(
        conflict.resource_type == EVALUATION_SUITE_RESOURCE_TYPE
        and conflict.resource_id == "portable.agent-suite@1.2"
        for conflict in preview.preview.conflicts
    )
