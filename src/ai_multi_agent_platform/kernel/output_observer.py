"""Provider-neutral post-commit observation of canonical Result/Artifact attachments.

The base kernel remains the lifecycle authority. This wrapper adds one optional async
observer seam that runs only after the canonical attachment event is durable. Replaying
the same idempotent attach command replays observation from the persisted event, which
allows callers to recover an interruption between attachment persistence and downstream
coordination without appending a second attachment event.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from ai_multi_agent_platform.contracts import ContractError, ErrorCode, PlatformEvent
from ai_multi_agent_platform.kernel.models import TaskState

from .kernel import PlatformKernel as _BasePlatformKernel


@runtime_checkable
class OutputAttachmentObserver(Protocol):
    """Observe an already-persisted canonical output attachment."""

    async def output_attached(self, event: PlatformEvent) -> None: ...


class OutputObservingPlatformKernel(_BasePlatformKernel):
    """PlatformKernel with an optional post-commit output observer boundary.

    The observer never participates in the canonical append transaction and therefore cannot
    become lifecycle truth. If observation fails, the attachment remains durable and retrying
    the same attach idempotency key replays the observer from that exact persisted event.
    """

    _output_attachment_observer: OutputAttachmentObserver | None = None

    def configure_output_attachment_observer(self, observer: OutputAttachmentObserver) -> None:
        """Bind one observer to this kernel instance without permitting silent replacement."""

        if not isinstance(observer, OutputAttachmentObserver):
            raise TypeError("output attachment observer does not satisfy the observer contract")
        current = self._output_attachment_observer
        if current is not None and current is not observer:
            raise ContractError(
                ErrorCode.CONFLICT,
                "output attachment observer is already configured",
            )
        self._output_attachment_observer = observer

    async def attach_result(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        result_id: str,
        run_id: str | None = None,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> TaskState:
        task = await super().attach_result(
            idempotency_key=idempotency_key,
            task_id=task_id,
            result_id=result_id,
            run_id=run_id,
            actor_ref=actor_ref,
            source=source,
        )
        observer = self._output_attachment_observer
        if observer is None:
            return task
        event = await self._canonical_attachment_event(
            task_id=task_id,
            idempotency_key=idempotency_key,
            event_type="result.attached",
            payload_key="result_id",
            output_id=result_id,
            run_id=run_id,
        )
        await observer.output_attached(event)
        return await self.get_task(task_id)

    async def attach_artifact(
        self,
        *,
        idempotency_key: str,
        task_id: str,
        artifact_id: str,
        run_id: str | None = None,
        actor_ref: str | None = None,
        source: str = "platform-kernel",
    ) -> TaskState:
        task = await super().attach_artifact(
            idempotency_key=idempotency_key,
            task_id=task_id,
            artifact_id=artifact_id,
            run_id=run_id,
            actor_ref=actor_ref,
            source=source,
        )
        observer = self._output_attachment_observer
        if observer is None:
            return task
        event = await self._canonical_attachment_event(
            task_id=task_id,
            idempotency_key=idempotency_key,
            event_type="artifact.attached",
            payload_key="artifact_id",
            output_id=artifact_id,
            run_id=run_id,
        )
        await observer.output_attached(event)
        return await self.get_task(task_id)

    async def _canonical_attachment_event(
        self,
        *,
        task_id: str,
        idempotency_key: str,
        event_type: str,
        payload_key: str,
        output_id: str,
        run_id: str | None,
    ) -> PlatformEvent:
        matches = tuple(
            event
            for event in await self.history(task_id)
            if event.event_type == event_type and event.causation_id == idempotency_key
        )
        if len(matches) != 1:
            raise ContractError(
                ErrorCode.CONTRACT_VIOLATION,
                "canonical attachment command does not resolve to exactly one persisted event",
                details={
                    "task_id": task_id,
                    "idempotency_key": idempotency_key,
                    "event_type": event_type,
                    "match_count": len(matches),
                },
            )
        event = matches[0]
        canonical_output_id = event.payload.get(payload_key)
        if canonical_output_id != output_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "attachment idempotency key was replayed with a different output",
                details={
                    "task_id": task_id,
                    "idempotency_key": idempotency_key,
                    "canonical_output_id": canonical_output_id,
                    "requested_output_id": output_id,
                },
            )
        expected_subject_type = "run" if run_id is not None else "task"
        expected_subject_id = run_id or task_id
        if event.subject_type != expected_subject_type or event.subject_id != expected_subject_id:
            raise ContractError(
                ErrorCode.CONFLICT,
                "attachment idempotency key was replayed with a different Run binding",
                details={
                    "task_id": task_id,
                    "idempotency_key": idempotency_key,
                    "canonical_subject_type": event.subject_type,
                    "canonical_subject_id": event.subject_id,
                    "requested_subject_type": expected_subject_type,
                    "requested_subject_id": expected_subject_id,
                },
            )
        return event


__all__ = ["OutputAttachmentObserver", "OutputObservingPlatformKernel"]
