from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.control_plane.http import HTTPRequest
from ai_multi_agent_platform.control_plane.notifications_live import (
    ControlPlaneASGI as NotificationControlPlaneASGI,
)
from ai_multi_agent_platform.control_plane.release_api import ControlPlaneASGI, ControlPlaneHTTP
from ai_multi_agent_platform.notifications.events import NotificationProjectingEventProvider
from ai_multi_agent_platform.observability import (
    FailureComponent,
    ObservabilityExporter,
    Telemetry,
    TelemetryContext,
    TraceHierarchy,
)
from ai_multi_agent_platform.observability.models import (
    MetricRecord,
    SpanRecord,
    StructuredLog,
    TimelineEntry,
)


class _ReleaseOperator:
    def status(self) -> dict[str, object]:
        return {}


class _HealthControlPlane:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error

    @property
    def registered_collections(self) -> tuple[str, ...]:
        return ()

    @property
    def registered_commands(self) -> tuple[str, ...]:
        return ()

    async def health(self) -> dict[str, object]:
        if self.error is not None:
            raise self.error
        return {"alive": True, "ready": True}


class _FailingExporter(ObservabilityExporter):
    def _fail(self) -> None:
        raise RuntimeError("provider_token=do-not-leak")

    def emit_log(self, record: StructuredLog) -> None:
        del record
        self._fail()

    def emit_metric(self, record: MetricRecord) -> None:
        del record
        self._fail()

    def emit_span(self, record: SpanRecord) -> None:
        del record
        self._fail()

    def emit_timeline(self, record: TimelineEntry) -> None:
        del record
        self._fail()


class _LifecycleControlPlane:
    def __init__(
        self,
        *,
        notification_start_error: BaseException | None = None,
        notification_stop_error: BaseException | None = None,
        automation_stop_error: BaseException | None = None,
    ) -> None:
        self.notification_start_error = notification_start_error
        self.notification_stop_error = notification_stop_error
        self.automation_stop_error = automation_stop_error
        self.calls: list[str] = []

    async def start_automation_runtime(self) -> None:
        self.calls.append("automation.start")

    async def start_notification_runtime(self) -> None:
        self.calls.append("notification.start")
        if self.notification_start_error is not None:
            raise self.notification_start_error

    async def stop_notification_runtime(self) -> None:
        self.calls.append("notification.stop")
        if self.notification_stop_error is not None:
            raise self.notification_stop_error

    async def stop_automation_runtime(self) -> None:
        self.calls.append("automation.stop")
        if self.automation_stop_error is not None:
            raise self.automation_stop_error


class _InnerEventProvider:
    def __init__(self) -> None:
        self.published = 0

    async def publish(self, event: object) -> None:
        del event
        self.published += 1


class _FailingNotificationProjection:
    def __init__(self, error: BaseException) -> None:
        self.error = error

    async def project_event(self, event: object) -> tuple[()]:
        del event
        raise self.error


def _http(control_plane: object) -> ControlPlaneHTTP:
    return ControlPlaneHTTP(control_plane, release_operator=_ReleaseOperator())


def _health_request() -> HTTPRequest:
    return HTTPRequest(
        method="GET",
        path="/api/v1/health",
        headers={
            "x-request-id": "request_boundary_test",
            "x-correlation-id": "correlation_boundary_test",
        },
    )


def _notification_lifespan_app(control_plane: object) -> NotificationControlPlaneASGI:
    app = object.__new__(NotificationControlPlaneASGI)
    app._control_plane = control_plane  # type: ignore[attr-defined]
    return app


async def _run_notification_lifespan(
    control_plane: object,
    message_type: str,
) -> list[dict[str, Any]]:
    app = _notification_lifespan_app(control_plane)
    sent: list[dict[str, Any]] = []
    received = False

    async def receive() -> dict[str, Any]:
        nonlocal received
        if received:
            raise AssertionError("lifespan requested an unexpected additional message")
        received = True
        return {"type": message_type}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    await app._lifespan(receive, send)
    return sent


def test_contract_error_preserves_canonical_public_contract_without_cause() -> None:
    try:
        raise RuntimeError("provider_token=super-secret")
    except RuntimeError as cause:
        error = ContractError(
            ErrorCode.RATE_LIMITED,
            "capacity is temporarily exhausted",
            retryable=True,
            details={"scope": "control-plane"},
        )
        error.__cause__ = cause

    response = asyncio.run(_http(_HealthControlPlane(error)).handle(_health_request()))

    assert response.status == 429
    assert response.body == {
        "code": "rate_limited",
        "category": "capacity",
        "message": "capacity is temporarily exhausted",
        "request_id": "request_boundary_test",
        "correlation_id": "correlation_boundary_test",
        "retryable": True,
        "details": {"scope": "control-plane"},
    }
    assert "super-secret" not in json.dumps(response.body)


def test_unexpected_exception_becomes_secret_safe_backend_error() -> None:
    response = asyncio.run(
        _http(
            _HealthControlPlane(
                RuntimeError("api_key=super-secret provider payload should not cross boundary")
            )
        ).handle(_health_request())
    )

    assert response.status == 502
    assert response.body == {
        "code": "backend_error",
        "category": "backend",
        "message": "internal platform operation failed",
        "request_id": "request_boundary_test",
        "correlation_id": "correlation_boundary_test",
        "retryable": False,
        "details": {"exception_type": "RuntimeError"},
    }
    serialized = json.dumps(response.body)
    assert "super-secret" not in serialized
    assert "provider payload" not in serialized


def test_public_http_does_not_translate_cancellation_or_process_control() -> None:
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_http(_HealthControlPlane(asyncio.CancelledError())).handle(_health_request()))

    with pytest.raises(SystemExit):
        asyncio.run(_http(_HealthControlPlane(SystemExit(7))).handle(_health_request()))


def test_asgi_disconnect_is_not_converted_to_an_api_failure() -> None:
    http = _http(_HealthControlPlane())
    app = ControlPlaneASGI(http)
    sent: list[dict[str, Any]] = []

    async def receive() -> dict[str, Any]:
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent.append(message)

    asyncio.run(
        app(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/tasks",
                "headers": [],
                "query_string": b"",
            },
            receive,
            send,
        )
    )

    assert sent == []


def test_asgi_route_semantics_precede_malformed_json_validation() -> None:
    app = ControlPlaneASGI(_http(_HealthControlPlane()))

    async def invoke(method: str, path: str, body: bytes) -> tuple[int, dict[str, Any]]:
        sent: list[dict[str, Any]] = []
        delivered = False

        async def receive() -> dict[str, Any]:
            nonlocal delivered
            if delivered:
                return {"type": "http.disconnect"}
            delivered = True
            return {
                "type": "http.request",
                "body": body,
                "more_body": False,
            }

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        await app(
            {
                "type": "http",
                "method": method,
                "path": path,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"x-request-id", b"request_route_before_body"),
                    (b"x-correlation-id", b"correlation_route_before_body"),
                ],
                "query_string": b"",
            },
            receive,
            send,
        )

        start = next(message for message in sent if message["type"] == "http.response.start")
        body_message = next(message for message in sent if message["type"] == "http.response.body")
        payload = json.loads(body_message["body"])
        assert isinstance(payload, dict)
        return int(start["status"]), payload

    async def scenario() -> None:
        wrong_method_status, wrong_method = await invoke(
            "POST",
            "/api/v1/openapi.json",
            b"{",
        )
        assert wrong_method_status == 405
        assert wrong_method["code"] == "method_not_allowed"
        assert wrong_method["category"] == "transport"
        assert wrong_method["request_id"] == "request_route_before_body"
        assert wrong_method["correlation_id"] == "correlation_route_before_body"

        unknown_status, unknown = await invoke(
            "POST",
            "/api/v1/does-not-exist/nested",
            b"{",
        )
        assert unknown_status == 404
        assert unknown["code"] == "not_found"
        assert unknown["category"] == "resource"

        valid_route_status, valid_route = await invoke(
            "POST",
            "/api/v1/projects",
            b"{",
        )
        assert valid_route_status == 400
        assert valid_route["code"] == "invalid_json"

        non_object_wrong_method_status, non_object_wrong_method = await invoke(
            "POST",
            "/api/v1/health",
            b"[]",
        )
        assert non_object_wrong_method_status == 405
        assert non_object_wrong_method["code"] == "method_not_allowed"

        task_command_wrong_method_status, task_command_wrong_method = await invoke(
            "GET",
            "/api/v1/tasks/task_1:queue",
            b"{",
        )
        assert task_command_wrong_method_status == 405
        assert task_command_wrong_method["code"] == "method_not_allowed"

        unknown_task_command_status, unknown_task_command = await invoke(
            "POST",
            "/api/v1/tasks/task_1:does-not-exist",
            b"{",
        )
        assert unknown_task_command_status == 404
        assert unknown_task_command["code"] == "not_found"

        run_cancel_wrong_method_status, run_cancel_wrong_method = await invoke(
            "GET",
            "/api/v1/tasks/task_1/runs/run_1:cancel",
            b"{",
        )
        assert run_cancel_wrong_method_status == 405
        assert run_cancel_wrong_method["code"] == "method_not_allowed"

        colon_model_item_wrong_method_status, colon_model_item_wrong_method = await invoke(
            "POST",
            "/api/v1/models/model_1:does-not-exist",
            b"{",
        )
        assert colon_model_item_wrong_method_status == 405
        assert colon_model_item_wrong_method["code"] == "method_not_allowed"

        colon_provider_item_wrong_method_status, colon_provider_item_wrong_method = await invoke(
            "POST",
            "/api/v1/model-providers/provider_1:does-not-exist",
            b"{",
        )
        assert colon_provider_item_wrong_method_status == 405
        assert colon_provider_item_wrong_method["code"] == "method_not_allowed"

        colon_model_identifier_status, colon_model_identifier = await invoke(
            "GET",
            "/api/v1/models/provider:model",
            b"{",
        )
        assert colon_model_identifier_status == 400
        assert colon_model_identifier["code"] == "invalid_json"

    asyncio.run(scenario())


def test_notification_startup_failure_rolls_back_and_redacts_lifespan_message() -> None:
    control_plane = _LifecycleControlPlane(
        notification_start_error=RuntimeError("token=startup-secret"),
        automation_stop_error=ValueError("password=rollback-secret"),
    )

    sent = asyncio.run(_run_notification_lifespan(control_plane, "lifespan.startup"))

    assert control_plane.calls == [
        "automation.start",
        "notification.start",
        "automation.stop",
    ]
    assert sent == [
        {
            "type": "lifespan.startup.failed",
            "message": (
                "notification runtime startup failed (RuntimeError); rollback failed (ValueError)"
            ),
        }
    ]
    assert "startup-secret" not in json.dumps(sent)
    assert "rollback-secret" not in json.dumps(sent)


def test_notification_startup_cancellation_rolls_back_then_propagates() -> None:
    control_plane = _LifecycleControlPlane(
        notification_start_error=asyncio.CancelledError(),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_run_notification_lifespan(control_plane, "lifespan.startup"))

    assert control_plane.calls == [
        "automation.start",
        "notification.start",
        "automation.stop",
    ]


def test_notification_shutdown_cancellation_still_settles_automation_then_propagates() -> None:
    control_plane = _LifecycleControlPlane(
        notification_stop_error=asyncio.CancelledError(),
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(_run_notification_lifespan(control_plane, "lifespan.shutdown"))

    assert control_plane.calls == ["notification.stop", "automation.stop"]


def test_notification_projection_reporting_failure_remains_secondary() -> None:
    inner = _InnerEventProvider()
    notifications = _FailingNotificationProjection(RuntimeError("projection-secret"))
    sink_calls = 0

    async def failing_sink(event: object, error: Exception) -> None:
        nonlocal sink_calls
        del event, error
        sink_calls += 1
        raise RuntimeError("telemetry-secret")

    provider = NotificationProjectingEventProvider(
        inner,  # type: ignore[arg-type]
        notifications,  # type: ignore[arg-type]
        projection_failure_sink=failing_sink,  # type: ignore[arg-type]
    )

    asyncio.run(provider.publish(object()))  # type: ignore[arg-type]

    assert inner.published == 1
    assert sink_calls == 1


def test_notification_projection_does_not_swallow_cancellation() -> None:
    inner = _InnerEventProvider()
    notifications = _FailingNotificationProjection(asyncio.CancelledError())
    provider = NotificationProjectingEventProvider(
        inner,  # type: ignore[arg-type]
        notifications,  # type: ignore[arg-type]
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(provider.publish(object()))  # type: ignore[arg-type]

    assert inner.published == 1


def test_telemetry_export_failure_does_not_replace_primary_error() -> None:
    telemetry = Telemetry(_FailingExporter())
    hierarchy = TraceHierarchy(telemetry)
    primary = ContractError(ErrorCode.TIMEOUT, "primary timeout", retryable=True)

    async def operation() -> None:
        raise primary

    with pytest.raises(ContractError) as caught:
        asyncio.run(
            hierarchy.observe(
                span_name="boundary.test",
                metric_prefix="platform.boundary.test",
                event_prefix="boundary.test",
                component=FailureComponent.INFRASTRUCTURE_UNKNOWN,
                context=TelemetryContext(correlation_id="correlation_boundary_test"),
                operation=operation,
            )
        )

    assert caught.value is primary
    assert telemetry.last_export_error == "RuntimeError"


def test_telemetry_export_failure_does_not_replace_cancellation() -> None:
    telemetry = Telemetry(_FailingExporter())
    hierarchy = TraceHierarchy(telemetry)

    async def operation() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            hierarchy.observe(
                span_name="boundary.cancelled",
                metric_prefix="platform.boundary.cancelled",
                event_prefix="boundary.cancelled",
                component=FailureComponent.INFRASTRUCTURE_UNKNOWN,
                context=TelemetryContext(correlation_id="correlation_boundary_test"),
                operation=operation,
            )
        )

    assert telemetry.last_export_error == "RuntimeError"


def test_strict_telemetry_exporter_failure_remains_explicit_opt_in() -> None:
    telemetry = Telemetry(_FailingExporter(), strict_exporter_errors=True)

    with pytest.raises(RuntimeError, match="do-not-leak"):
        telemetry.metric(
            "platform.boundary.strict",
            1.0,
            context=TelemetryContext(correlation_id="correlation_boundary_test"),
        )
