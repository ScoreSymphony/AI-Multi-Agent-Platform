from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

from ai_multi_agent_platform.automation import (
    Automation,
    AutomationService,
    AutomationStartupRecoveryDisposition,
    AutomationState,
    DeliveryStatus,
    IdentityContext,
    InMemoryAutomationRepository,
    TaskTemplate,
    TriggerDefinition,
    TriggerDelivery,
    TriggerType,
)
from ai_multi_agent_platform.deployment.startup_recovery import (
    StartupRecoveryExtensionReport,
    reconcile_single_node_startup,
)
from ai_multi_agent_platform.deployment.transient_recovery import (
    SingleNodeTransientStateRecoveryExtension,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import BrowserSession, LocalUserAccount
from ai_multi_agent_platform.security.sqlite_authentication import SqliteAuthenticationStore


NOW = datetime(2026, 9, 19, 1, 30, tzinfo=UTC)


def _identity() -> IdentityContext:
    return IdentityContext(
        principal_ref="user:issue-1153",
        owner_type="user",
        owner_id="issue-1153",
    )


def _template() -> TaskTemplate:
    return TaskTemplate(
        title="Restart-safe work",
        objective="Prove stale transient Automation state is reconciled",
    )


async def _automation(service: AutomationService) -> Automation:
    return await service.create_automation(
        name="issue-1153",
        description="startup reconciliation",
        identity=_identity(),
        trigger=TriggerDefinition(type=TriggerType.MANUAL),
        task_template=_template(),
        now=NOW,
    )


def _delivery(
    automation_id: str,
    *,
    status: DeliveryStatus,
    attempt: int,
    dedupe_key: str,
) -> TriggerDelivery:
    return replace(
        TriggerDelivery.create(
            automation_id=automation_id,
            trigger_type=TriggerType.MANUAL,
            source="manual-test",
            dedupe_key=dedupe_key,
            fired_at=NOW,
        ),
        status=status,
        attempt=attempt,
    )


def test_processing_delivery_replays_same_attempt_and_idempotency_identity() -> None:
    async def scenario() -> None:
        repository = InMemoryAutomationRepository()
        calls: list[str] = []
        canonical_tasks: dict[str, str] = {}

        async def creator(*args: object) -> str:
            idempotency_key = cast(str, args[3])
            calls.append(idempotency_key)
            return canonical_tasks.setdefault(idempotency_key, new_id("task"))

        service = AutomationService(repository=repository, task_creator=creator)
        automation = await _automation(service)
        stale = _delivery(
            automation.id,
            status=DeliveryStatus.PROCESSING,
            attempt=1,
            dedupe_key="manual:crash-window",
        )
        await repository.save_delivery(stale)
        key = f"automation:{automation.id}:{stale.dedupe_key}"
        existing_task_id = new_id("task")
        canonical_tasks[key] = existing_task_id

        report = await service.reconcile_startup_deliveries()
        recovered = await service.get_delivery(stale.id)

        assert report.ready_for_service is True
        assert len(report.records) == 1
        assert report.records[0].disposition is AutomationStartupRecoveryDisposition.RESUMED
        assert recovered.status is DeliveryStatus.SUCCEEDED
        assert recovered.attempt == 1
        assert recovered.generated_task_id == existing_task_id
        assert calls == [key]
        assert canonical_tasks == {key: existing_task_id}

        repeated = await service.reconcile_startup_deliveries()
        assert repeated.records == ()
        assert await service.get_delivery(stale.id) == recovered

    asyncio.run(scenario())


def test_pending_delivery_owned_by_inactive_automation_is_settled_without_execution() -> None:
    async def scenario() -> None:
        repository = InMemoryAutomationRepository()
        calls = 0

        async def creator(*args: object) -> str:
            nonlocal calls
            calls += 1
            return new_id("task")

        service = AutomationService(repository=repository, task_creator=creator)
        automation = await _automation(service)
        await service.set_state(automation.id, AutomationState.DISABLED, now=NOW)
        stale = _delivery(
            automation.id,
            status=DeliveryStatus.PENDING,
            attempt=0,
            dedupe_key="manual:pending-before-crash",
        )
        await repository.save_delivery(stale)

        report = await service.reconcile_startup_deliveries()
        settled = await service.get_delivery(stale.id)

        assert report.ready_for_service is True
        assert report.records[0].disposition is (
            AutomationStartupRecoveryDisposition.SETTLED_INACTIVE_PENDING
        )
        assert settled.status is DeliveryStatus.REJECTED
        assert settled.error_code == "startup_recovery_owner_inactive"
        assert settled.attempt == 0
        assert calls == 0
        assert (await service.reconcile_startup_deliveries()).records == ()

    asyncio.run(scenario())


def test_processing_delivery_with_inactive_owner_blocks_instead_of_guessing() -> None:
    async def scenario() -> None:
        repository = InMemoryAutomationRepository()

        async def creator(*args: object) -> str:
            raise AssertionError("inactive uncertain work must not execute")

        service = AutomationService(repository=repository, task_creator=creator)
        automation = await _automation(service)
        await service.set_state(automation.id, AutomationState.PAUSED, now=NOW)
        stale = _delivery(
            automation.id,
            status=DeliveryStatus.PROCESSING,
            attempt=1,
            dedupe_key="manual:uncertain-inactive",
        )
        await repository.save_delivery(stale)

        report = await service.reconcile_startup_deliveries()

        assert report.ready_for_service is False
        assert len(report.blockers) == 1
        assert report.blockers[0].disposition is (
            AutomationStartupRecoveryDisposition.BLOCKED_INACTIVE_PROCESSING
        )
        assert (await service.get_delivery(stale.id)).status is DeliveryStatus.PROCESSING

    asyncio.run(scenario())


def test_orphaned_automation_delivery_blocks_readiness_without_mutation() -> None:
    async def scenario() -> None:
        repository = InMemoryAutomationRepository()

        async def creator(*args: object) -> str:
            raise AssertionError("orphaned work must not execute")

        service = AutomationService(repository=repository, task_creator=creator)
        stale = _delivery(
            new_id("automation"),
            status=DeliveryStatus.PENDING,
            attempt=0,
            dedupe_key="manual:orphaned-owner",
        )
        await repository.save_delivery(stale)

        report = await service.reconcile_startup_deliveries()

        assert report.ready_for_service is False
        assert report.blockers[0].disposition is (
            AutomationStartupRecoveryDisposition.BLOCKED_ORPHANED_OWNER
        )
        assert await service.get_delivery(stale.id) == stale

    asyncio.run(scenario())


def test_transient_extension_reports_durable_auth_session_authority() -> None:
    async def scenario() -> None:
        repository = InMemoryAutomationRepository()

        async def creator(*args: object) -> str:
            return new_id("task")

        service = AutomationService(repository=repository, task_creator=creator)
        sessions = {
            "active": BrowserSession(
                session_id="session-active",
                user_id="user-active",
                token_verifier="verifier",
                csrf_verifier="csrf",
                created_at=NOW - timedelta(minutes=5),
                authenticated_at=NOW - timedelta(minutes=5),
                expires_at=NOW + timedelta(minutes=5),
            ),
            "expired": BrowserSession(
                session_id="session-expired",
                user_id="user-expired",
                token_verifier="verifier",
                csrf_verifier="csrf",
                created_at=NOW - timedelta(hours=2),
                authenticated_at=NOW - timedelta(hours=2),
                expires_at=NOW - timedelta(hours=1),
            ),
            "revoked": BrowserSession(
                session_id="session-revoked",
                user_id="user-revoked",
                token_verifier="verifier",
                csrf_verifier="csrf",
                created_at=NOW - timedelta(minutes=5),
                authenticated_at=NOW - timedelta(minutes=5),
                expires_at=NOW + timedelta(minutes=5),
                revoked_at=NOW - timedelta(minutes=1),
            ),
        }
        extension = SingleNodeTransientStateRecoveryExtension(
            automation=service,
            authentication_sessions=sessions,
            clock=lambda: NOW,
        )

        report = await extension.reconcile_startup()

        assert report.ready_for_service is True
        assert report.items_checked == 3
        auth = next(
            item for item in report.evidence if item["state_class"] == "authentication_session"
        )
        assert auth["disposition"] == "durable_authority_revalidated"
        assert auth["active"] == 1
        assert auth["expired"] == 1
        assert auth["revoked"] == 1

    asyncio.run(scenario())



def test_auth_session_expiry_and_revocation_survive_sqlite_restart(tmp_path: Path) -> None:
    path = tmp_path / "authentication.sqlite3"
    store = SqliteAuthenticationStore(path)
    user = LocalUserAccount(
        user_id="user-issue-1153",
        username="issue-1153",
        password_verifier="verifier",
        enabled=True,
        locked=False,
        created_at=NOW - timedelta(days=1),
        password_changed_at=NOW - timedelta(days=1),
    )
    store.users[user.user_id] = user
    expired = BrowserSession(
        session_id="session-expired-restart",
        user_id=user.user_id,
        token_verifier="expired-verifier",
        csrf_verifier="expired-csrf",
        created_at=NOW - timedelta(hours=2),
        authenticated_at=NOW - timedelta(hours=2),
        expires_at=NOW - timedelta(hours=1),
    )
    revoked = BrowserSession(
        session_id="session-revoked-restart",
        user_id=user.user_id,
        token_verifier="revoked-verifier",
        csrf_verifier="revoked-csrf",
        created_at=NOW - timedelta(minutes=10),
        authenticated_at=NOW - timedelta(minutes=10),
        expires_at=NOW + timedelta(hours=1),
        revoked_at=NOW - timedelta(minutes=1),
    )
    store.sessions[expired.session_id] = expired
    store.sessions[revoked.session_id] = revoked

    restarted = SqliteAuthenticationStore(path)

    restored_expired = restarted.sessions[expired.session_id]
    restored_revoked = restarted.sessions[revoked.session_id]
    assert restored_expired.expires_at == expired.expires_at
    assert restored_expired.active(now=NOW) is False
    assert restored_revoked.revoked_at == revoked.revoked_at
    assert restored_revoked.active(now=NOW) is False


def test_startup_report_persists_extension_evidence(
    tmp_path: Path,
) -> None:
    class EmptyKernel:
        async def recover_all(self) -> tuple[object, ...]:
            return ()

    class EvidenceExtension:
        async def reconcile_startup(self) -> StartupRecoveryExtensionReport:
            return StartupRecoveryExtensionReport(
                name="issue-1153-test",
                items_checked=1,
                evidence=(
                    {
                        "state_class": "automation_delivery",
                        "disposition": "resumed",
                    },
                ),
            )

    result = asyncio.run(
        reconcile_single_node_startup(
            data_dir=tmp_path,
            kernel=cast(Any, EmptyKernel()),
            extensions=(EvidenceExtension(),),
        )
    )

    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert result.ready_for_service is True
    assert payload["extensions"][0]["evidence"] == [
        {
            "state_class": "automation_delivery",
            "disposition": "resumed",
        }
    ]
