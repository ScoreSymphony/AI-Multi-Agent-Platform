"""Canonical Control Plane timeline enrichment supplied by issue #16."""

from __future__ import annotations

from typing import Any

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.observability import (
    FailureComponent,
    TimelineReader,
    timeline_entry_resource,
)

from .models import PageQuery, RequestContext, paginate
from .run_contract import ControlPlane as _RunControlPlane
from .run_contract import ControlPlaneHTTP as _RunControlPlaneHTTP
from .run_contract import build_openapi as _build_run_openapi
from .service import _event_resource


class ControlPlane(_RunControlPlane):
    """Current Control Plane plus a derived, backend-neutral observability timeline."""

    _observability_timeline: TimelineReader | None = None

    def bind_observability_timeline(self, timeline: TimelineReader | None) -> None:
        """Bind or disable the derived telemetry reader without changing lifecycle ownership."""

        self._observability_timeline = timeline

    async def timeline(
        self,
        context: RequestContext,
        task_id: str,
        query: PageQuery,
    ) -> dict[str, JsonValue]:
        task = await self._kernel.get_task(task_id)
        await self._authorize_for_task(context, "event:list", task_id, task)
        resources = [_event_resource(event) for event in await self._events.read_events(task_id)]
        telemetry = self._observability_timeline
        if telemetry is not None:
            resources.extend(
                timeline_entry_resource(entry)
                for entry in telemetry.query_timeline(task_id=task_id)
                if entry.component is not FailureComponent.DOMAIN_KERNEL
            )
        return paginate(resources, query)


class ControlPlaneHTTP(_RunControlPlaneHTTP):
    """HTTP mapping uses the same canonical timeline route with richer derived entries."""


def build_openapi(
    *,
    extension_collections: tuple[str, ...] = (),
    extension_commands: tuple[str, ...] = (),
) -> dict[str, Any]:
    return _build_run_openapi(
        extension_collections=extension_collections,
        extension_commands=extension_commands,
    )
