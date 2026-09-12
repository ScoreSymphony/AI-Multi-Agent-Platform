"""Control Plane resource and command surface for issue #872 coding batches."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, cast

from ai_multi_agent_platform.coding_batches.models import (
    BatchAggregationPolicy,
    CodingBatch,
    CodingWorkItem,
)
from ai_multi_agent_platform.coding_batches.projection import coding_batch_resource
from ai_multi_agent_platform.coding_batches.secured import CodingBatchCoordinator
from ai_multi_agent_platform.contracts import ContractError, ErrorCode
from ai_multi_agent_platform.contracts.types import JsonValue

from .extensions import CommandHandler, ResourceService
from .models import PageQuery, RequestContext

CODING_BATCH_COLLECTION = "coding-batches"
CODING_BATCH_COMMANDS = (
    "coding-batch.create",
    "coding-batch.integration-candidate",
)


class CodingBatchCatalog(Protocol):
    """Read boundary required by the Control Plane projection."""

    def get(self, batch_id: str) -> CodingBatch | None: ...

    def list_batches(self) -> tuple[CodingBatch, ...]: ...


class CodingBatchResourceService(ResourceService):
    """Expose persisted #872 orchestration state without treating Git refs as lifecycle truth."""

    def __init__(self, catalog: CodingBatchCatalog) -> None:
        self._catalog = catalog

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context, query
        return tuple(_resource(batch) for batch in self._catalog.list_batches())

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        batch = self._catalog.get(resource_id)
        if batch is None:
            raise ContractError(ErrorCode.NOT_FOUND, f"coding batch not found: {resource_id}")
        return _resource(batch)


class CodingBatchCommandHandlers:
    """Mutation helpers behind the authenticated/authorized generic command seam."""

    def __init__(self, coordinator: CodingBatchCoordinator) -> None:
        self._coordinator = coordinator

    async def create(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        del resource_ref
        if context.idempotency_key is None:
            raise ContractError(ErrorCode.INVALID_REQUEST, "idempotency key is required")
        try:
            policy = BatchAggregationPolicy(
                _optional_string(payload, "aggregation_policy")
                or BatchAggregationPolicy.ALL_REQUIRED.value
            )
        except ValueError as exc:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "unsupported coding-batch aggregation_policy",
            ) from exc
        concurrency_limit = _optional_positive_int(payload, "concurrency_limit") or 4
        items_raw = payload.get("work_items")
        if not isinstance(items_raw, list) or not items_raw:
            raise ContractError(
                ErrorCode.INVALID_REQUEST,
                "coding-batch work_items must be a non-empty array",
            )
        batch = self._coordinator.create_batch(
            request_key=context.idempotency_key,
            repository_id=_required_string(payload, "repository_id"),
            target_ref=_required_string(payload, "target_ref"),
            base_revision=_required_string(payload, "base_revision"),
            work_items=tuple(_parse_work_item(item) for item in items_raw),
            aggregation_policy=policy,
            concurrency_limit=concurrency_limit,
        )
        return _resource(batch)

    async def integration_candidate(
        self,
        context: RequestContext,
        resource_ref: str,
        payload: dict[str, JsonValue],
    ) -> dict[str, JsonValue]:
        if context.idempotency_key is None:
            raise ContractError(ErrorCode.INVALID_REQUEST, "idempotency key is required")
        workstream_ids = _optional_string_tuple(payload, "workstream_ids")
        try:
            self._coordinator.build_integration_candidate(
                resource_ref,
                current_target_revision=_required_string(payload, "current_target_revision"),
                workstream_ids=workstream_ids,
            )
            batch = self._coordinator.get(resource_ref)
        except KeyError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"coding batch not found: {resource_ref}",
            ) from exc
        except ValueError as exc:
            raise ContractError(ErrorCode.INVALID_REQUEST, str(exc)) from exc
        return _resource(batch)


def coding_batch_resource_services(catalog: CodingBatchCatalog) -> dict[str, ResourceService]:
    return {CODING_BATCH_COLLECTION: CodingBatchResourceService(catalog)}


def coding_batch_command_handlers(
    coordinator: CodingBatchCoordinator,
) -> dict[str, CommandHandler]:
    handlers = CodingBatchCommandHandlers(coordinator)
    return {
        "coding-batch.create": handlers.create,
        "coding-batch.integration-candidate": handlers.integration_candidate,
    }


def _resource(batch: CodingBatch) -> dict[str, JsonValue]:
    return cast(dict[str, JsonValue], coding_batch_resource(batch))


def _parse_work_item(raw_value: JsonValue) -> CodingWorkItem:
    if not isinstance(raw_value, dict):
        raise ContractError(ErrorCode.INVALID_REQUEST, "coding-batch work item must be an object")
    raw = cast(Mapping[str, JsonValue], raw_value)
    metadata_raw = raw.get("metadata", {})
    if not isinstance(metadata_raw, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in metadata_raw.items()
    ):
        raise ContractError(
            ErrorCode.INVALID_REQUEST,
            "coding-batch work item metadata must be a string map",
        )
    return CodingWorkItem(
        work_item_id=_required_string(raw, "work_item_id"),
        task_id=_required_string(raw, "task_id"),
        plan_id=_required_string(raw, "plan_id"),
        step_id=_required_string(raw, "step_id"),
        dependencies=_string_tuple(raw, "dependencies"),
        affected_paths=_string_tuple(raw, "affected_paths"),
        semantic_scopes=_string_tuple(raw, "semantic_scopes"),
        metadata=cast(dict[str, str], metadata_raw),
    )


def _required_string(payload: Mapping[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value.strip()


def _optional_string(payload: Mapping[str, JsonValue], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a non-blank string")
    return value.strip()


def _optional_positive_int(payload: Mapping[str, JsonValue], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be a positive integer")
    return value


def _string_tuple(payload: Mapping[str, JsonValue], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ContractError(ErrorCode.INVALID_REQUEST, f"{key} must be an array of strings")
    return tuple(cast(str, item).strip() for item in value)


def _optional_string_tuple(
    payload: Mapping[str, JsonValue],
    key: str,
) -> tuple[str, ...] | None:
    if payload.get(key) is None:
        return None
    return _string_tuple(payload, key)


__all__ = [
    "CODING_BATCH_COLLECTION",
    "CODING_BATCH_COMMANDS",
    "CodingBatchCatalog",
    "CodingBatchCommandHandlers",
    "CodingBatchResourceService",
    "coding_batch_command_handlers",
    "coding_batch_resource_services",
]
