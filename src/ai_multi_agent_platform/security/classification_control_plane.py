"""Safe Control Plane projection of the canonical data-classification vocabulary."""

from __future__ import annotations

from ai_multi_agent_platform.contracts import (
    ContractError,
    DataClassification,
    ErrorCode,
    JsonValue,
    classification_strength,
)
from ai_multi_agent_platform.control_plane.extensions import ControlPlane
from ai_multi_agent_platform.control_plane.models import PageQuery, RequestContext

DATA_CLASSIFICATION_COLLECTION = "data-classifications"

_CANONICAL = frozenset(
    {
        DataClassification.PUBLIC,
        DataClassification.INTERNAL,
        DataClassification.CONFIDENTIAL,
        DataClassification.SECRET,
        DataClassification.REGULATED,
    }
)
_COMPATIBILITY = frozenset(
    {
        DataClassification.PRIVATE,
        DataClassification.RESTRICTED,
        DataClassification.SECRET_REFERENCE,
    }
)


class DataClassificationResourceService:
    """Read-only, content-free vocabulary view for Web/CLI/Control Plane consumers."""

    search_indexable = False

    async def list_resources(
        self,
        context: RequestContext,
        query: PageQuery,
    ) -> tuple[dict[str, JsonValue], ...]:
        del context
        canonical_only = False
        if query.filters is not None and "canonical" in query.filters:
            raw = query.filters["canonical"]
            if not isinstance(raw, bool):
                raise ContractError(
                    ErrorCode.INVALID_REQUEST,
                    "canonical classification filter must be a boolean",
                )
            canonical_only = raw
        values = tuple(
            item
            for item in DataClassification
            if not canonical_only or item in _CANONICAL
        )
        return tuple(_classification_resource(item) for item in values)

    async def get_resource(
        self,
        context: RequestContext,
        resource_id: str,
    ) -> dict[str, JsonValue]:
        del context
        try:
            classification = DataClassification(resource_id)
        except ValueError as exc:
            raise ContractError(
                ErrorCode.NOT_FOUND,
                f"data classification not found: {resource_id}",
            ) from exc
        return _classification_resource(classification)


def register_data_classification_control_plane(control_plane: ControlPlane) -> None:
    control_plane.register_resource_service(
        DATA_CLASSIFICATION_COLLECTION,
        DataClassificationResourceService(),
    )


def _classification_resource(value: DataClassification) -> dict[str, JsonValue]:
    return {
        "id": value.value,
        "classification": value.value,
        "strength": classification_strength(value),
        "canonical": value in _CANONICAL,
        "compatibility_vocabulary": value in _COMPATIBILITY,
        "secret_subsystem_owned": value
        in {DataClassification.SECRET, DataClassification.SECRET_REFERENCE},
        "schema_version": "data-classification/v1",
    }


__all__ = [
    "DATA_CLASSIFICATION_COLLECTION",
    "DataClassificationResourceService",
    "register_data_classification_control_plane",
]
