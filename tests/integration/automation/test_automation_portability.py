from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ai_multi_agent_platform.automation.models import (
    Automation,
    AutomationState,
    IdentityContext,
    TaskTemplate,
    TriggerDefinition,
    TriggerDelivery,
    TriggerType,
)
from ai_multi_agent_platform.automation.repository import (
    InMemoryAutomationRepository,
    SqliteAutomationRepository,
)
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.portability import (
    AUTOMATION_RESOURCE_TYPE,
    AutomationImportMutationHandler,
    AutomationPortableSnapshot,
    DependencyKind,
    ExclusionCategory,
    IdPolicy,
    ImportContext,
    ImportExecutor,
    ImportMutationRegistry,
    ImportPreviewService,
    PackageProvenance,
    ResourceSerializerRegistry,
    automation_runtime_exclusions,
    build_package,
    register_automation_portability_codec,
    snapshot_automation,
)

_NOW = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)


def _identity(user_id: str = "user-a") -> IdentityContext:
    return IdentityContext(
        principal_ref=f"user:{user_id}",
        owner_type="user",
        owner_id=user_id,
    )


def _recurring_automation(
    *,
    project_id: str | None = None,
    workspace_id: str | None = None,
    identity: IdentityContext | None = None,
) -> Automation:
    automation = Automation.create(
        name="Portable recurring automation",
        description="Create a canonical task on schedule",
        identity=identity or _identity(),
        trigger=TriggerDefinition(
            type=TriggerType.RECURRING,
            at=_NOW + timedelta(hours=1),
            interval_seconds=3600,
            timezone="Europe/Berlin",
        ),
        task_template=TaskTemplate(
            title="Portable task",
            objective="Exercise canonical portability",
            project_id=project_id,
            workspace_id=workspace_id,
            payload={"labels": ["portable"]},
        ),
        project_id=project_id,
        workspace_id=workspace_id,
        now=_NOW,
    )
    return replace(
        automation,
        last_evaluated_at=_NOW + timedelta(minutes=30),
        next_evaluation_at=_NOW + timedelta(hours=1),
    )


def test_enabled_automation_snapshot_excludes_scheduler_and_delivery_runtime_state() -> None:
    automation = _recurring_automation(project_id=new_id("project"))

    snapshot = snapshot_automation(automation)
    exclusions = automation_runtime_exclusions(automation.id)

    assert snapshot.source_state is AutomationState.ENABLED
    assert snapshot.automation.state is AutomationState.PAUSED
    assert snapshot.automation.last_evaluated_at is None
    assert snapshot.automation.next_evaluation_at is None
    assert {item.path for item in exclusions} == {
        "$.automation.scheduler_progress",
        "$.automation.trigger_deliveries",
    }
    assert all(item.category is ExclusionCategory.BACKEND_RUNTIME_STATE for item in exclusions)

    registry = ResourceSerializerRegistry()
    register_automation_portability_codec(registry)
    resource = registry.serialize(AUTOMATION_RESOURCE_TYPE, snapshot)

    encoded = resource.payload["automation"]
    assert isinstance(encoded, dict)
    assert "last_evaluated_at" not in encoded
    assert "next_evaluation_at" not in encoded
    assert "trigger_deliveries" not in str(resource.payload)
    assert resource.payload["activation_required"] is True
    assert resource.payload["runtime_state_included"] is False


def test_webhook_verification_ref_is_a_secret_dependency_not_secret_material() -> None:
    project_id = new_id("project")
    automation = Automation.create(
        name="Portable webhook",
        description="Webhook definition",
        identity=_identity(),
        trigger=TriggerDefinition(
            type=TriggerType.WEBHOOK,
            webhook_source="github",
            verification_ref="secret://github/webhook",
        ),
        task_template=TaskTemplate(
            title="Webhook task",
            objective="Handle verified event",
            project_id=project_id,
        ),
        project_id=project_id,
        now=_NOW,
    )
    registry = ResourceSerializerRegistry()
    register_automation_portability_codec(registry)

    resource = registry.serialize(AUTOMATION_RESOURCE_TYPE, snapshot_automation(automation))

    secret_dependencies = tuple(
        item for item in resource.dependencies if item.kind is DependencyKind.SECRET
    )
    assert len(secret_dependencies) == 1
    assert secret_dependencies[0].identifier == "secret://github/webhook"
    assert secret_dependencies[0].required is True
    assert "api_key" not in str(resource.payload)
    assert "token" not in str(resource.payload).casefold()


def test_automation_and_scope_references_are_deterministically_remapped() -> None:
    source_project = new_id("project")
    source_workspace = new_id("workspace")
    target_project = new_id("project")
    target_workspace = new_id("workspace")
    target_automation = new_id("automation")
    automation = _recurring_automation(
        project_id=source_project,
        workspace_id=source_workspace,
    )
    registry = ResourceSerializerRegistry()
    register_automation_portability_codec(registry)
    resource = registry.serialize(AUTOMATION_RESOURCE_TYPE, snapshot_automation(automation))

    decoded = registry.deserialize(
        resource,
        ImportContext(
            id_mapping={
                (AUTOMATION_RESOURCE_TYPE, automation.id): target_automation,
                ("project", source_project): target_project,
                ("workspace", source_workspace): target_workspace,
            }
        ),
    )

    assert isinstance(decoded, AutomationPortableSnapshot)
    assert decoded.automation.id == target_automation
    assert decoded.automation.project_id == target_project
    assert decoded.automation.workspace_id == target_workspace
    assert decoded.automation.task_template.project_id == target_project
    assert decoded.automation.task_template.workspace_id == target_workspace


def test_enabled_automation_import_stays_paused_until_explicit_activation() -> None:
    project_id = new_id("project")
    automation = _recurring_automation(project_id=project_id)
    registry = ResourceSerializerRegistry()
    register_automation_portability_codec(registry, id_policy=IdPolicy.REGENERATE)
    resource = registry.serialize(AUTOMATION_RESOURCE_TYPE, snapshot_automation(automation))
    package = build_package(
        source_platform_version="0.0.1",
        resources=(resource,),
        provenance=PackageProvenance(source="automation-portability-test"),
        excluded_state=automation_runtime_exclusions(automation.id),
    )
    preview = ImportPreviewService(
        resource_exists=lambda _resource_type, _resource_id: False,
        dependency_available=lambda _requirement: True,
    ).preview(package)
    target = InMemoryAutomationRepository()
    mutations = ImportMutationRegistry()
    mutations.register(AutomationImportMutationHandler(target, _identity()))

    result = asyncio.run(ImportExecutor(registry, mutations).execute(package, preview))

    target_id = preview.mapping_dict()[(AUTOMATION_RESOURCE_TYPE, automation.id)]
    assert target_id.startswith("automation_")
    imported = asyncio.run(target.get_automation(target_id))
    assert result.resources[0].target_id == target_id
    assert imported.state is AutomationState.PAUSED
    assert imported.last_evaluated_at is None
    assert imported.next_evaluation_at is None
    assert asyncio.run(target.list_deliveries(target_id)) == ()


def test_automation_identity_transfer_is_rejected_before_mutation() -> None:
    automation = _recurring_automation(identity=_identity("user-a"))
    registry = ResourceSerializerRegistry()
    register_automation_portability_codec(registry)
    resource = registry.serialize(AUTOMATION_RESOURCE_TYPE, snapshot_automation(automation))
    package = build_package(
        source_platform_version="0.0.1",
        resources=(resource,),
        provenance=PackageProvenance(source="automation-portability-test"),
    )
    preview = ImportPreviewService(
        resource_exists=lambda _resource_type, _resource_id: False,
        dependency_available=lambda _requirement: True,
    ).preview(package)
    target = InMemoryAutomationRepository()
    mutations = ImportMutationRegistry()
    mutations.register(AutomationImportMutationHandler(target, _identity("user-b")))

    with pytest.raises(ContractError) as failed:
        asyncio.run(ImportExecutor(registry, mutations).execute(package, preview))

    assert failed.value.code is ErrorCode.FORBIDDEN
    with pytest.raises(ContractError) as missing:
        asyncio.run(target.get_automation(automation.id))
    assert missing.value.code is ErrorCode.NOT_FOUND


def test_automation_import_handler_rolls_back_new_definition() -> None:
    automation = _recurring_automation()
    snapshot = snapshot_automation(automation)
    registry = ResourceSerializerRegistry()
    register_automation_portability_codec(registry)
    resource = registry.serialize(AUTOMATION_RESOURCE_TYPE, snapshot)
    decoded = registry.deserialize(resource)
    assert isinstance(decoded, AutomationPortableSnapshot)
    target = InMemoryAutomationRepository()
    handler = AutomationImportMutationHandler(target, _identity())
    context = ImportContext()

    token = asyncio.run(handler.apply(resource, decoded, context))
    assert token == automation.id
    asyncio.run(target.get_automation(automation.id))

    asyncio.run(handler.rollback(resource, decoded, token, context))
    with pytest.raises(ContractError) as missing:
        asyncio.run(target.get_automation(automation.id))
    assert missing.value.code is ErrorCode.NOT_FOUND


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_guarded_compensation_never_removes_automation_with_delivery_history(
    backend: str,
    tmp_path: Path,
) -> None:
    repository = (
        InMemoryAutomationRepository()
        if backend == "memory"
        else SqliteAutomationRepository(tmp_path / "automation.sqlite3")
    )
    automation = _recurring_automation()
    asyncio.run(repository.save_automation(automation))
    delivery = TriggerDelivery.create(
        automation_id=automation.id,
        trigger_type=automation.trigger.type,
        source="scheduler",
        dedupe_key="scheduled:1",
        fired_at=_NOW + timedelta(hours=1),
    )
    asyncio.run(repository.save_delivery(delivery))

    with pytest.raises(ContractError) as failed:
        asyncio.run(repository.remove_automation_if_unused(automation.id))

    assert failed.value.code is ErrorCode.CONFLICT
    assert asyncio.run(repository.get_automation(automation.id)) == automation
    assert asyncio.run(repository.list_deliveries(automation.id)) == (delivery,)


def test_invalid_automation_remains_non_running_with_invalidation_provenance() -> None:
    source = _recurring_automation().invalidate(
        "policy.changed",
        _NOW + timedelta(hours=2),
    )

    snapshot = snapshot_automation(source)

    assert snapshot.source_state is AutomationState.INVALID
    assert snapshot.automation.state is AutomationState.INVALID
    assert snapshot.automation.invalidation_reason_code == "policy.changed"
    assert snapshot.automation.invalidated_at == source.invalidated_at
    assert snapshot.automation.state_before_invalid is AutomationState.ENABLED
    assert snapshot.automation.last_evaluated_at is None
    assert snapshot.automation.next_evaluation_at is None
