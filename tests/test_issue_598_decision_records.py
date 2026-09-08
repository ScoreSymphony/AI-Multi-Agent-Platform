from __future__ import annotations

import asyncio
from dataclasses import replace

import pytest

from ai_multi_agent_platform.contracts import ContractError
from ai_multi_agent_platform.contracts.types import AuthorizationDecision, AuthorizationRequest
from ai_multi_agent_platform.control_plane import ControlPlane
from ai_multi_agent_platform.control_plane.models import ActorContext, PageQuery, RequestContext
from ai_multi_agent_platform.decisions import (
    DecisionAlternative,
    DecisionAlternativeStatus,
    DecisionOutcome,
    DecisionRecord,
    DecisionReference,
    DecisionService,
    DecisionStatus,
    SqliteDecisionRepository,
)
from ai_multi_agent_platform.decisions.control_plane import (
    DECISION_COLLECTION,
    decision_record_command_handlers,
    decision_record_resource_services,
)
from ai_multi_agent_platform.decisions.portability import (
    export_decision_bundle,
    import_decision_bundle,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.kernel import InMemoryKernelRepository, PlatformKernel
from ai_multi_agent_platform.testing import (
    FakeAuthorizationProvider,
    FakeLifecycleBackend,
    FakeOrchestrator,
)


class DenyDecisionMutations(FakeAuthorizationProvider):
    async def authorize(self, request: AuthorizationRequest) -> AuthorizationDecision:
        self.calls.append(request)
        if request.action.startswith("decision-record."):
            return AuthorizationDecision(allowed=False, reason="decision mutation denied")
        return AuthorizationDecision(allowed=True, reason="read allowed")


def _record(*, outcome: DecisionOutcome = DecisionOutcome.ADOPT) -> DecisionRecord:
    selected = outcome in {
        DecisionOutcome.ADOPT,
        DecisionOutcome.EXPERIMENTAL,
        DecisionOutcome.CUSTOM,
    }
    research = DecisionReference(
        kind="research-item",
        resource_id="research_item_example",
        revision=3,
        digest="research-digest-v3",
    )
    evaluation = DecisionReference(
        kind="evaluation-run",
        resource_id="evaluation_run_example",
        revision=2,
        digest="evaluation-digest-v2",
    )
    return DecisionRecord(
        title="Choose orchestration strategy",
        subject="orchestration strategy",
        category="architecture-runtime",
        scope_type="platform",
        question="Which orchestration strategy should be the current baseline?",
        alternatives=(
            DecisionAlternative(
                label="Hermes adapter",
                status=(
                    DecisionAlternativeStatus.SELECTED
                    if selected
                    else DecisionAlternativeStatus.CONSIDERED
                ),
                evidence_refs=(research, evaluation),
                trade_offs=("adapter maintenance",),
            ),
            DecisionAlternative(
                label="Custom orchestration core",
                status=DecisionAlternativeStatus.REJECTED,
                evidence_refs=(research,),
                unknowns=("long-term maintenance cost",),
            ),
        ),
        outcome=outcome,
        rationale="The selected option best fits current evidence and replaceability constraints.",
        actor_ref="user:decision-maker",
        evidence_refs=(research,),
        evaluation_refs=(evaluation,),
    )


def test_create_decision_with_two_alternatives_and_exact_research_evaluation_refs(tmp_path) -> None:
    service = DecisionService(SqliteDecisionRepository(tmp_path / "decisions.sqlite3"))
    created = service.create(_record())

    assert len(created.record.alternatives) == 2
    assert created.record.evidence_refs[0].revision == 3
    assert created.record.evaluation_refs[0].digest == "evaluation-digest-v2"
    assert created.status is DecisionStatus.CURRENT


@pytest.mark.parametrize(
    "outcome", [DecisionOutcome.ADOPT, DecisionOutcome.REJECT, DecisionOutcome.DEFER]
)
def test_outcome_vocabulary_supports_adopt_reject_and_defer(
    tmp_path, outcome: DecisionOutcome
) -> None:
    service = DecisionService(SqliteDecisionRepository(tmp_path / f"{outcome.value}.sqlite3"))
    assert service.create(_record(outcome=outcome)).record.outcome is outcome


def test_supersession_keeps_original_record_immutable(tmp_path) -> None:
    repository = SqliteDecisionRepository(tmp_path / "decisions.sqlite3")
    service = DecisionService(repository)
    original = service.create(_record()).record
    original_digest = original.content_digest

    replacement = replace(
        original,
        id=new_id("decision_record"),
        outcome=DecisionOutcome.EXPERIMENTAL,
        rationale="New evaluation supports a time-bounded experiment.",
        supersedes=original.id,
        revision=2,
        content_digest="",
    )
    current = service.supersede(original.id, replacement)

    persisted_old = repository.get(original.id)
    assert persisted_old == original
    assert persisted_old.content_digest == original_digest
    assert service.view(original.id).status is DecisionStatus.SUPERSEDED
    assert service.view(original.id).superseded_by == current.record.id
    assert service.supersession_chain(current.record.id)[0].record.id == original.id


def test_rejected_or_superseded_decision_cannot_drive_new_action_provenance(tmp_path) -> None:
    service = DecisionService(SqliteDecisionRepository(tmp_path / "decisions.sqlite3"))
    rejected = service.create(_record(outcome=DecisionOutcome.REJECT))
    target = DecisionReference(kind="model-provider", resource_id="provider_local")

    with pytest.raises(ContractError):
        service.link_downstream_provenance(rejected.record.id, target)

    adopted = service.create(_record())
    replacement = replace(
        adopted.record,
        id=new_id("decision_record"),
        supersedes=adopted.record.id,
        revision=2,
        content_digest="",
    )
    service.supersede(adopted.record.id, replacement)
    with pytest.raises(ContractError):
        service.link_downstream_provenance(adopted.record.id, target)


def test_downstream_action_stores_decision_provenance_without_activation(tmp_path) -> None:
    service = DecisionService(SqliteDecisionRepository(tmp_path / "decisions.sqlite3"))
    decision = service.create(_record())
    target = DecisionReference(kind="plugin", resource_id="plugin_example", revision="1.2.0")

    linked = service.link_downstream_provenance(decision.record.id, target)
    provenance = service.action_provenance(decision.record.id)

    assert linked.downstream_refs == (target,)
    assert provenance["decision_record_id"] == decision.record.id
    assert provenance["decision_digest"] == decision.record.content_digest
    assert "enabled" not in provenance


def test_import_export_preserves_references_and_never_has_activation_semantics(tmp_path) -> None:
    source = DecisionService(SqliteDecisionRepository(tmp_path / "source.sqlite3"))
    created = source.create(_record())
    target = DecisionReference(kind="provider", resource_id="provider_local", digest="provider-v1")
    source.link_downstream_provenance(created.record.id, target)
    bundle = export_decision_bundle(source)

    assert bundle["activation_semantics"] == "none"

    restored = DecisionService(SqliteDecisionRepository(tmp_path / "restored.sqlite3"))
    imported = import_decision_bundle(restored, bundle, actor_ref="user:importer")
    view = restored.view(created.record.id)

    assert imported == (created.record.id,)
    assert view.record.evidence_refs == created.record.evidence_refs
    assert view.record.evaluation_refs == created.record.evaluation_refs
    assert view.downstream_refs == (target,)


def test_resource_service_visibility_prevents_scope_existence_leak(tmp_path) -> None:
    service = DecisionService(SqliteDecisionRepository(tmp_path / "decisions.sqlite3"))
    visible = service.create(_record()).record
    hidden = service.create(
        replace(_record(), id=new_id("decision_record"), content_digest="")
    ).record

    async def visibility(context: RequestContext, view: object) -> bool:
        del context
        return view.record.id == visible.id

    resources = decision_record_resource_services(service, visibility=visibility)[
        DECISION_COLLECTION
    ]
    context = RequestContext(request_id="request", correlation_id="correlation")
    listed = asyncio.run(resources.list_resources(context, PageQuery()))

    assert [item["id"] for item in listed] == [visible.id]
    with pytest.raises(ContractError):
        asyncio.run(resources.get_resource(context, hidden.id))


def test_control_plane_denies_unauthorized_decision_mutation(tmp_path) -> None:
    decisions = DecisionService(SqliteDecisionRepository(tmp_path / "decisions.sqlite3"))
    repository = InMemoryKernelRepository()
    kernel = PlatformKernel(
        orchestrator=FakeOrchestrator(),
        lifecycle=FakeLifecycleBackend(),
        repository=repository,
    )
    authorization = DenyDecisionMutations()
    control_plane = ControlPlane(
        kernel=kernel,
        events=repository,
        authorization=authorization,
        resource_services=decision_record_resource_services(decisions),
        command_handlers=decision_record_command_handlers(decisions),
    )
    context = RequestContext(
        request_id="request",
        correlation_id="correlation",
        actor=ActorContext(principal_ref="user:blocked"),
        idempotency_key="decision-create-1",
    )

    with pytest.raises(ContractError):
        asyncio.run(
            control_plane.execute_command(
                context,
                "decision-record.create",
                DECISION_COLLECTION,
                {"title": "blocked"},
            )
        )

    assert authorization.calls[-1].action == "decision-record.create"
    assert decisions.list_views() == ()
