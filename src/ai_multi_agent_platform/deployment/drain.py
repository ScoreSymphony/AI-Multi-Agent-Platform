"""Process-local graceful-drain policy for the supported single-node Control Plane.

The drain gate is deliberately not durable lifecycle authority. It only stops new process-local
admission while an operator shutdown is in progress. Canonical unfinished work remains owned by its
normal subsystem and is reconciled after restart by the existing startup-recovery path.
Provenance: #707 defines the startup-recovery authority reused here.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass
from enum import StrEnum
from typing import Any
from uuid import uuid4

from ai_multi_agent_platform.contracts import ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.control_plane import ControlPlaneASGI, HTTPRequest, HTTPResponse
from ai_multi_agent_platform.control_plane.first_user_bootstrap import (
    AuthenticatedControlPlaneHTTP,
)
from ai_multi_agent_platform.control_plane.http import (
    ASGIReceive,
    ASGISend,
    _decode_asgi_headers,
    _send_response,
)
from ai_multi_agent_platform.control_plane.models import API_VERSION, APIException
from ai_multi_agent_platform.observability import (
    FailureComponent,
    Telemetry,
    TelemetryContext,
    TelemetryOutcome,
    TelemetrySeverity,
)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_ASGI_MUTATION_ADMITTED: ContextVar[object | None] = ContextVar(
    "single_node_drain_asgi_mutation_admitted",
    default=None,
)


class SingleNodeDrainState(StrEnum):
    """Transient process admission state; never persisted as canonical lifecycle state."""

    SERVING = "serving"
    DRAINING = "draining"


@dataclass(frozen=True, slots=True)
class SingleNodeDrainSnapshot:
    state: SingleNodeDrainState
    active_mutations: int
    timeout_seconds: float
    forced: bool
    completed: bool
    reason: str | None = None
    force_reason: str | None = None

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "state": self.state.value,
            "active_mutations": self.active_mutations,
            "timeout_seconds": self.timeout_seconds,
            "forced": self.forced,
            "completed": self.completed,
            "reason": self.reason,
            "force_reason": self.force_reason,
        }


class SingleNodeDrainController:
    """Coordinate bounded process-local mutation admission during single-node shutdown."""

    def __init__(self, *, timeout_seconds: float, telemetry: Telemetry) -> None:
        if timeout_seconds <= 0:
            raise ValueError("single-node drain timeout must be positive")
        self.timeout_seconds = float(timeout_seconds)
        self._telemetry = telemetry
        self._condition = asyncio.Condition()
        self._state = SingleNodeDrainState.SERVING
        self._active_mutations = 0
        self._deadline: float | None = None
        self._reason: str | None = None
        self._force_reason: str | None = None
        self._forced = False
        self._force_timed_out = False
        self._completed = False
        self._quiesce_callbacks: list[Callable[[], None]] = []

    @property
    def draining(self) -> bool:
        return self._state is SingleNodeDrainState.DRAINING

    @property
    def active_mutations(self) -> int:
        return self._active_mutations

    def snapshot(self) -> SingleNodeDrainSnapshot:
        return SingleNodeDrainSnapshot(
            state=self._state,
            active_mutations=self._active_mutations,
            timeout_seconds=self.timeout_seconds,
            forced=self._forced,
            completed=self._completed,
            reason=self._reason,
            force_reason=self._force_reason,
        )

    def register_quiesce_callback(self, callback: Callable[[], None]) -> None:
        """Register process-local autonomous admission to stop when drain begins."""
        if callback not in self._quiesce_callbacks:
            self._quiesce_callbacks.append(callback)

    async def begin(self, *, reason: str = "process_shutdown") -> bool:
        """Enter drain exactly once and establish the shared shutdown deadline."""

        entered = False
        async with self._condition:
            if self._state is SingleNodeDrainState.SERVING:
                self._state = SingleNodeDrainState.DRAINING
                self._reason = reason
                self._deadline = asyncio.get_running_loop().time() + self.timeout_seconds
                entered = True
                self._condition.notify_all()
        if entered:
            for callback in tuple(self._quiesce_callbacks):
                try:
                    callback()
                # error-boundary: allow-broad-catch=boundary drain admission stays closed
                except Exception as exc:
                    self._event(
                        "platform.single_node.drain.teardown_failed",
                        severity=TelemetrySeverity.ERROR,
                        outcome=TelemetryOutcome.FAILED,
                        attributes={
                            "stage": "autonomous_quiesce",
                            "detail": type(exc).__name__,
                        },
                    )
            self._event(
                "platform.single_node.drain.requested",
                attributes={
                    "reason": reason,
                    "active_mutations": self._active_mutations,
                    "timeout_seconds": self.timeout_seconds,
                },
            )
            self._event(
                "platform.single_node.drain.entered",
                attributes={
                    "mutable_admission": "disabled",
                    "active_mutations": self._active_mutations,
                },
            )
            self._event(
                "platform.single_node.drain.mutable_admission_disabled",
                attributes={"active_mutations": self._active_mutations},
            )
            self._metric_active_mutations(disposition="in_flight_at_drain_entry")
        return entered

    async def try_admit_mutation(self) -> bool:
        """Atomically admit a mutation only while the process is authoritative for new work."""

        async with self._condition:
            if self._state is SingleNodeDrainState.DRAINING:
                return False
            self._active_mutations += 1
            return True

    async def release_mutation(self) -> None:
        async with self._condition:
            if self._active_mutations <= 0:
                raise RuntimeError("single-node drain mutation accounting underflow")
            self._active_mutations -= 1
            self._condition.notify_all()

    async def wait_for_inflight(self) -> bool:
        """Allow already-admitted mutations to settle only within the shared drain deadline."""

        async with self._condition:
            while self._active_mutations:
                remaining = self._remaining_seconds()
                if remaining <= 0:
                    break
                try:
                    await asyncio.wait_for(self._condition.wait(), timeout=remaining)
                except TimeoutError:
                    break
            settled = self._active_mutations == 0

        if settled:
            self._metric_active_mutations(disposition="completed_during_drain")
            return True

        await self.mark_forced("in_flight_mutation_timeout", timed_out=True)
        self._metric_active_mutations(disposition="deferred_to_startup_reconciliation")
        return False

    def remaining_seconds(self) -> float:
        if self._deadline is None:
            return self.timeout_seconds
        return max(0.0, self._remaining_seconds())

    async def mark_forced(self, reason: str, *, timed_out: bool = False) -> None:
        newly_forced = False
        async with self._condition:
            if not self._forced:
                self._forced = True
                self._force_reason = reason
                self._force_timed_out = timed_out
                newly_forced = True
                self._condition.notify_all()
        if newly_forced:
            outcome = TelemetryOutcome.TIMED_OUT if timed_out else TelemetryOutcome.FAILED
            self._event(
                "platform.single_node.drain.forced",
                severity=TelemetrySeverity.WARNING,
                outcome=outcome,
                attributes={
                    "reason": reason,
                    "active_mutations": self._active_mutations,
                },
            )
            if timed_out:
                self._event(
                    "platform.single_node.drain.timeout",
                    severity=TelemetrySeverity.WARNING,
                    outcome=TelemetryOutcome.TIMED_OUT,
                    attributes={
                        "reason": reason,
                        "active_mutations": self._active_mutations,
                    },
                )

    def mark_teardown_failure(self, detail: str) -> None:
        self._event(
            "platform.single_node.drain.teardown_failed",
            severity=TelemetrySeverity.ERROR,
            outcome=TelemetryOutcome.FAILED,
            attributes={"detail": detail[:512]},
        )

    async def mark_completed(self) -> None:
        completed = False
        async with self._condition:
            if not self._completed:
                self._completed = True
                completed = True
                self._condition.notify_all()
        if completed:
            if self._forced and self._force_timed_out:
                outcome = TelemetryOutcome.TIMED_OUT
            elif self._forced:
                outcome = TelemetryOutcome.FAILED
            else:
                outcome = TelemetryOutcome.SUCCEEDED
            self._event(
                "platform.single_node.drain.completed",
                outcome=outcome,
                attributes={
                    "forced": self._forced,
                    "active_mutations": self._active_mutations,
                    "force_reason": self._force_reason,
                },
            )

    def _remaining_seconds(self) -> float:
        if self._deadline is None:
            return self.timeout_seconds
        return self._deadline - asyncio.get_running_loop().time()

    def _metric_active_mutations(self, *, disposition: str) -> None:
        self._best_effort_telemetry(
            lambda: self._telemetry.metric(
                "platform.single_node.drain.in_flight",
                float(self._active_mutations),
                context=TelemetryContext(),
                attributes={"disposition": disposition},
            )
        )

    def _event(
        self,
        event_name: str,
        *,
        severity: TelemetrySeverity = TelemetrySeverity.INFO,
        outcome: TelemetryOutcome = TelemetryOutcome.UNKNOWN,
        attributes: dict[str, JsonValue] | None = None,
    ) -> None:
        context = TelemetryContext()
        self._best_effort_telemetry(
            lambda: self._telemetry.log(
                severity=severity,
                component=FailureComponent.INFRASTRUCTURE_UNKNOWN,
                event_name=event_name,
                context=context,
                outcome=outcome,
                attributes=attributes,
            )
        )
        self._best_effort_telemetry(
            lambda: self._telemetry.timeline(
                event_name=event_name,
                component=FailureComponent.INFRASTRUCTURE_UNKNOWN,
                context=context,
                outcome=outcome,
                attributes=attributes,
            )
        )

    @staticmethod
    def _best_effort_telemetry(emit: Callable[[], None]) -> None:
        try:
            emit()
        # error-boundary: allow-broad-catch=boundary observability cannot own drain lifecycle
        except Exception:
            return


class DrainAwareAuthenticatedControlPlaneHTTP(AuthenticatedControlPlaneHTTP):
    """Apply one northbound mutation-admission gate without owning canonical state."""

    def __init__(
        self,
        *args: Any,
        drain: SingleNodeDrainController,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._drain = drain

    async def handle(self, request: HTTPRequest) -> HTTPResponse:
        method = request.method.upper()
        if method not in _SAFE_METHODS and _ASGI_MUTATION_ADMITTED.get() is not self._drain:
            if not await self._drain.try_admit_mutation():
                return self._draining_response(request)
            try:
                response = await super().handle(request)
            finally:
                await self._drain.release_mutation()
        else:
            response = await super().handle(request)

        if method == "GET" and _is_health_path(request.path) and self._drain.draining:
            return _overlay_draining_health(
                response,
                self._drain,
                readiness=_is_readiness_path(request.path),
            )
        return response

    def _draining_response(self, request: HTTPRequest) -> HTTPResponse:
        request_id = _request_header(request.headers, "x-request-id") or f"request_{uuid4()}"
        correlation_id = _request_header(request.headers, "x-correlation-id") or request_id
        return self._error_response(
            APIException(
                status=503,
                code=ErrorCode.UNAVAILABLE.value,
                message="single-node Control Plane is draining; new mutations are not admitted",
                retryable=True,
                details={
                    "draining": True,
                    "active_mutations": self._drain.active_mutations,
                },
            ),
            request_id,
            correlation_id,
        )


class SingleNodeDrainASGI(ControlPlaneASGI):
    """Bound the complete ASGI lifespan teardown with the shared single-node drain deadline."""

    def __init__(
        self,
        http: DrainAwareAuthenticatedControlPlaneHTTP,
        drain: SingleNodeDrainController,
    ) -> None:
        super().__init__(http)
        self._drain = drain

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        scope_type = scope.get("type")
        if scope_type == "http" and str(scope.get("method", "GET")).upper() not in _SAFE_METHODS:
            await self._handle_http_mutation(scope, receive, send)
            return
        if scope_type == "websocket":
            await self._handle_websocket(scope, receive, send)
            return
        if scope_type == "lifespan":
            await self._handle_lifespan(scope, receive, send)
            return
        await super().__call__(scope, receive, send)

    async def _handle_http_mutation(
        self,
        scope: dict[str, Any],
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        if not await self._drain.try_admit_mutation():
            request = HTTPRequest(
                method=str(scope.get("method", "POST")).upper(),
                path=str(scope.get("path", "/")),
                headers=_decode_asgi_headers(scope.get("headers", [])),
            )
            await _send_response(self._http._draining_response(request), send)
            return

        token = _ASGI_MUTATION_ADMITTED.set(self._drain)
        try:
            await super().__call__(scope, receive, send)
        finally:
            _ASGI_MUTATION_ADMITTED.reset(token)
            await self._drain.release_mutation()

    async def _handle_websocket(
        self,
        scope: dict[str, Any],
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        if not await self._drain.try_admit_mutation():
            connect = await receive()
            if connect.get("type") != "websocket.connect":
                await send(
                    {
                        "type": "websocket.close",
                        "code": 1002,
                        "reason": "websocket.connect required",
                    }
                )
                return
            await send(
                {
                    "type": "websocket.close",
                    "code": 1013,
                    "reason": "single-node Control Plane is draining",
                }
            )
            return

        try:
            await super().__call__(scope, receive, send)
        finally:
            await self._drain.release_mutation()

    async def _handle_lifespan(
        self,
        scope: dict[str, Any],
        receive: ASGIReceive,
        send: ASGISend,
    ) -> None:
        shutdown_seen = asyncio.Event()
        shutdown_response_sent = False

        async def drain_receive() -> dict[str, Any]:
            message = await receive()
            if message.get("type") == "lifespan.shutdown":
                await self._drain.begin(reason="asgi_lifespan_shutdown")
                await self._drain.wait_for_inflight()
                shutdown_seen.set()
            return message

        async def tracked_send(message: dict[str, Any]) -> None:
            nonlocal shutdown_response_sent
            message_type = message.get("type")
            if message_type == "lifespan.shutdown.complete":
                shutdown_response_sent = True
                await self._drain.mark_completed()
            elif message_type == "lifespan.shutdown.failed":
                shutdown_response_sent = True
                detail = str(message.get("message", "ASGI lifespan shutdown failed"))
                self._drain.mark_teardown_failure(detail)
                await self._drain.mark_forced("resource_teardown_failure")
                await self._drain.mark_completed()
            await send(message)

        base_call = super().__call__
        inner_task = asyncio.create_task(
            base_call(scope, drain_receive, tracked_send),
            name="single-node-drain-lifespan",
        )
        shutdown_waiter = asyncio.create_task(
            shutdown_seen.wait(),
            name="single-node-drain-shutdown-waiter",
        )
        await self._await_lifespan_shutdown(
            inner_task,
            shutdown_waiter,
            send=send,
            shutdown_response_sent=lambda: shutdown_response_sent,
        )

    async def _await_lifespan_shutdown(
        self,
        inner_task: asyncio.Task[None],
        shutdown_waiter: asyncio.Task[bool],
        *,
        send: ASGISend,
        shutdown_response_sent: Callable[[], bool],
    ) -> None:
        done, _ = await asyncio.wait(
            {inner_task, shutdown_waiter},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if inner_task in done:
            await _cancel_and_settle(shutdown_waiter)
            await inner_task
            return

        await _cancel_and_settle(shutdown_waiter)
        try:
            await asyncio.wait_for(
                asyncio.shield(inner_task),
                timeout=self._drain.remaining_seconds(),
            )
        except TimeoutError:
            await self._force_lifespan_completion(
                inner_task,
                send=send,
                response_sent=shutdown_response_sent,
                reason="resource_teardown_timeout",
                timed_out=True,
            )
        except asyncio.CancelledError:
            raise
        # error-boundary: allow-broad-catch=boundary lifespan owner must settle process teardown
        except Exception as exc:
            self._drain.mark_teardown_failure(type(exc).__name__)
            await self._force_lifespan_completion(
                inner_task,
                send=send,
                response_sent=shutdown_response_sent,
                reason="resource_teardown_failure",
                timed_out=False,
            )

    async def _force_lifespan_completion(
        self,
        inner_task: asyncio.Task[None],
        *,
        send: ASGISend,
        response_sent: Callable[[], bool],
        reason: str,
        timed_out: bool,
    ) -> None:
        await self._drain.mark_forced(reason, timed_out=timed_out)
        if not inner_task.done():
            inner_task.cancel()

        # The shared deadline has already expired. Give cooperative cancellation one loop turn,
        # but never await the teardown task without a bound after that point. A resource owner
        # that suppresses cancellation must not regain authority over process exit.
        await asyncio.sleep(0)

        def observe_completion(task: asyncio.Future[None]) -> None:
            if task.cancelled():
                return
            try:
                failure = task.exception()
            except asyncio.CancelledError:
                return
            if failure is not None:
                self._drain.mark_teardown_failure(type(failure).__name__)

        if inner_task.done():
            observe_completion(inner_task)
        else:
            self._drain.mark_teardown_failure("lifespan_teardown_did_not_settle_after_cancel")
            inner_task.add_done_callback(observe_completion)

        if not response_sent():
            await send({"type": "lifespan.shutdown.complete"})
        await self._drain.mark_completed()


async def _cancel_and_settle(task: asyncio.Task[Any]) -> None:
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def _request_header(headers: Mapping[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def _is_health_path(path: str) -> bool:
    normalized = path.rstrip("/")
    return normalized in {
        f"/api/{API_VERSION}/health",
        f"/api/{API_VERSION}/readiness",
    }


def _is_readiness_path(path: str) -> bool:
    return path.rstrip("/") == f"/api/{API_VERSION}/readiness"


def _overlay_draining_health(
    response: HTTPResponse,
    drain: SingleNodeDrainController,
    *,
    readiness: bool,
) -> HTTPResponse:
    if not isinstance(response.body, dict):
        return response
    body: dict[str, JsonValue] = dict(response.body)
    body["status"] = "draining"
    body["ready"] = False
    body["draining"] = True
    body["drain"] = drain.snapshot().to_json()
    return HTTPResponse(
        status=503 if readiness else 200,
        body=body,
        headers=dict(response.headers),
    )


__all__ = [
    "DrainAwareAuthenticatedControlPlaneHTTP",
    "SingleNodeDrainASGI",
    "SingleNodeDrainController",
    "SingleNodeDrainSnapshot",
    "SingleNodeDrainState",
]
