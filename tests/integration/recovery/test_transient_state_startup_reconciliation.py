from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

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
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.deployment import SingleNodeConfig, build_single_node_deployment
from ai_multi_agent_platform.deployment.startup_recovery import (
    StartupRecoveryExtensionReport,
    reconcile_single_node_startup,
)
from ai_multi_agent_platform.deployment.transient_recovery import (
    SingleNodeTransientStateRecoveryExtension,
)
from ai_multi_agent_platform.domain import new_id
from ai_multi_agent_platform.security import (
    AuthenticationError,
    AuthenticationFailure,
    BrowserSession,
    LocalAuthenticationService,
    LocalUserAccount,
)
from ai_multi_agent_platform.security.sqlite_authentication import SqliteAuthenticationStore


NOW = datetime(2026, 9, 19, 1, 30, tzinfo=UTC)


def _identity() -> IdentityContext:
    return IdentityContext(
        principal_ref="user:transient-recovery",
        owner_type="user",
        owner_id="transient-recovery",
    )


def _template() -> TaskTemplate:
    return TaskTemplate(
        title="Restart-safe work",
        objective="Prove stale transient Automation state is reconciled",
    )


async def _automation(service: AutomationService) -> Automation:
    return await service.create_automation(
        name="transient-state-recovery",
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


def test_single_node_restart_reconciles_crash_after_canonical_task_admission_without_duplicate(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        root = tmp_path / "single-node-crash-window"
        config = SingleNodeConfig(data_dir=root, secure_cookie=False)
        first = build_single_node_deployment(config)
        account = first.bootstrap_admin("transient-recovery-admin", "correct horse battery staple")
        automation = await first.control_plane.automation_service.create_automation(
            name="restart task admission",
            description="simulate process loss after canonical Task creation",
            identity=IdentityContext(
                principal_ref=account.user_id,
                owner_type="user",
                owner_id=account.user_id,
            ),
            trigger=TriggerDefinition(type=TriggerType.MANUAL),
            task_template=TaskTemplate(
                title="Canonical restart task",
                objective="Must not be duplicated when Automation delivery is replayed",
            ),
            now=NOW,
        )
        succeeded = await first.control_plane.automation_service.test_trigger(
            automation.id,
            occurrence_id="crash-after-task-admission",
            fired_at=NOW,
        )
        assert succeeded.status is DeliveryStatus.SUCCEEDED
        assert succeeded.generated_task_id is not None
        canonical_task_id = succeeded.generated_task_id

        # Simulate the exact crash window after canonical Task admission but before the
        # Automation owner persisted SUCCEEDED. The durable Task/idempotency record already exists.
        stale = replace(
            succeeded,
            status=DeliveryStatus.PROCESSING,
            generated_task_id=None,
            processing_duration_ms=None,
            error_code=None,
            error_message=None,
            retryable=False,
            next_retry_at=None,
            retry_exhausted_at=None,
        )
        await first.control_plane.automation_service.repository.save_delivery(stale)

        restarted = build_single_node_deployment(config)
        before_streams = set(await restarted.kernel_repository.list_stream_ids())
        assert canonical_task_id in before_streams

        recovery = await reconcile_single_node_startup(
            data_dir=root,
            kernel=restarted.kernel,
            coordinator=restarted.coordination,
            distributed_runtime=restarted.distributed_runtime,
            extensions=restarted.startup_recovery_extensions,
            reviewer_reconciler=restarted.reviewer_recovery,
        )
        recovered = await restarted.control_plane.automation_service.get_delivery(stale.id)
        after_streams = set(await restarted.kernel_repository.list_stream_ids())

        assert recovery.ready_for_service is True
        transient = next(
            report
            for report in recovery.extension_recoveries
            if report.name == "single-node-transient-state"
        )
        automation_evidence = next(
            item
            for item in transient.evidence
            if item.get("state_class") == "automation_delivery"
        )
        assert automation_evidence["disposition"] == "resumed"
        assert recovered.status is DeliveryStatus.SUCCEEDED
        assert recovered.attempt == succeeded.attempt
        assert recovered.generated_task_id == canonical_task_id
        assert after_streams == before_streams

        repeated = await reconcile_single_node_startup(
            data_dir=root,
            kernel=restarted.kernel,
            coordinator=restarted.coordination,
            distributed_runtime=restarted.distributed_runtime,
            extensions=restarted.startup_recovery_extensions,
            reviewer_reconciler=restarted.reviewer_recovery,
        )
        repeated_transient = next(
            report
            for report in repeated.extension_recoveries
            if report.name == "single-node-transient-state"
        )
        assert not any(
            item.get("state_class") == "automation_delivery"
            for item in repeated_transient.evidence
        )
        assert set(await restarted.kernel_repository.list_stream_ids()) == before_streams

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
        user_id="user-transient-recovery",
        username="transient-recovery",
        password_verifier="verifier",
        enabled=True,
        locked=False,
        created_at=NOW - timedelta(days=1),
        password_changed_at=NOW - timedelta(days=1),
    )
    store.users[user.user_id] = user
    authentication = LocalAuthenticationService(
        store=store,
        session_ttl=timedelta(hours=1),
    )
    expired = authentication.create_browser_session(
        user.user_id,
        now=NOW - timedelta(hours=2),
    )
    revoked = authentication.create_browser_session(
        user.user_id,
        now=NOW - timedelta(minutes=10),
    )
    authentication.revoke_session(
        user.user_id,
        revoked.session_id,
        now=NOW - timedelta(minutes=1),
    )

    restarted_store = SqliteAuthenticationStore(path)
    restarted = LocalAuthenticationService(
        store=restarted_store,
        session_ttl=timedelta(hours=1),
    )

    with pytest.raises(AuthenticationError) as expired_error:
        restarted.authenticate_session(expired.token, now=NOW)
    assert expired_error.value.failure is AuthenticationFailure.SESSION_EXPIRED

    with pytest.raises(AuthenticationError) as revoked_error:
        restarted.authenticate_session(revoked.token, now=NOW)
    assert revoked_error.value.failure is AuthenticationFailure.SESSION_REVOKED


def test_cleanup_failure_is_machine_readable_and_blocks_readiness(tmp_path: Path) -> None:
    class FailingAutomation:
        async def reconcile_startup_deliveries(self) -> Any:
            raise ContractError(
                ErrorCode.BACKEND_ERROR,
                "sensitive backend detail must not become startup authority",
            )

    class EmptyKernel:
        async def recover_all(self) -> tuple[object, ...]:
            return ()

    extension = SingleNodeTransientStateRecoveryExtension(
        automation=cast(Any, FailingAutomation()),
        authentication_sessions={},
        clock=lambda: NOW,
    )
    result = asyncio.run(
        reconcile_single_node_startup(
            data_dir=tmp_path,
            kernel=cast(Any, EmptyKernel()),
            extensions=(extension,),
        )
    )

    payload = json.loads(result.report_path.read_text(encoding="utf-8"))
    extension_payload = payload["extensions"][0]
    assert result.ready_for_service is False
    assert extension_payload["ready_for_service"] is False
    assert extension_payload["failure_count"] == 1
    assert extension_payload["failures"] == [
        {
            "state_class": "automation_delivery",
            "disposition": "blocked_reconciliation_error",
            "error_code": ErrorCode.BACKEND_ERROR.value,
        }
    ]
    assert "sensitive backend detail" not in json.dumps(payload)
    assert extension_payload["evidence"][0]["disposition"] == "blocked"
    assert extension_payload["evidence"][0]["duration_ms"] >= 0


def test_unexpected_cleanup_failure_also_fails_closed_without_exception_details() -> None:
    class FailingAutomation:
        async def reconcile_startup_deliveries(self) -> Any:
            raise RuntimeError("do-not-leak-this-detail")

    extension = SingleNodeTransientStateRecoveryExtension(
        automation=cast(Any, FailingAutomation()),
        authentication_sessions={},
        clock=lambda: NOW,
    )
    report = asyncio.run(extension.reconcile_startup())

    assert report.ready_for_service is False
    assert report.failures == (
        {
            "state_class": "automation_delivery",
            "disposition": "blocked_unexpected_reconciliation_error",
            "error_code": "unexpected_reconciliation_error",
        },
    )
    assert "do-not-leak-this-detail" not in repr(report)


def test_startup_report_persists_extension_evidence(
    tmp_path: Path,
) -> None:
    class EmptyKernel:
        async def recover_all(self) -> tuple[object, ...]:
            return ()

    class EvidenceExtension:
        async def reconcile_startup(self) -> StartupRecoveryExtensionReport:
            return StartupRecoveryExtensionReport(
                name="transient-recovery-evidence",
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
