"""Durable immutable Context Bundle storage for issue #590."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import cast

from ai_multi_agent_platform.contracts.types import JsonValue

from .models import (
    ContextBudget,
    ContextBudgetUsage,
    ContextBundle,
    ContextDataClassification,
    ContextEntry,
    ContextEntryRole,
    ContextFreshness,
    ContextOmission,
    ContextOmissionReason,
    ContextSourceRef,
    ContextSourceType,
    ContextTransformation,
    ContextTransformationKind,
    ContextTrust,
)


class InMemoryContextBundleRepository:
    """Immutable repository indexed by canonical ID and deterministic digest."""

    def __init__(self) -> None:
        self._bundles: dict[str, ContextBundle] = {}
        self._by_digest: dict[str, str] = {}

    def put(self, bundle: ContextBundle) -> ContextBundle:
        current = self._bundles.get(bundle.context_bundle_id)
        if current is not None:
            if current.digest != bundle.digest:
                raise ValueError("context bundle identity is immutable")
            return current
        existing_id = self._by_digest.get(bundle.digest)
        if existing_id is not None:
            return self._bundles[existing_id]
        self._bundles[bundle.context_bundle_id] = bundle
        self._by_digest[bundle.digest] = bundle.context_bundle_id
        return bundle

    def get(self, context_bundle_id: str) -> ContextBundle:
        try:
            return self._bundles[context_bundle_id]
        except KeyError as exc:
            raise KeyError(f"context bundle not found: {context_bundle_id}") from exc

    def get_by_digest(self, digest: str) -> ContextBundle | None:
        context_bundle_id = self._by_digest.get(digest)
        return None if context_bundle_id is None else self._bundles[context_bundle_id]

    def list_for_run(self, run_id: str) -> tuple[ContextBundle, ...]:
        return tuple(
            sorted(
                (bundle for bundle in self._bundles.values() if bundle.run_id == run_id),
                key=lambda item: (item.created_at, item.context_bundle_id),
            )
        )

    def list_all(self) -> tuple[ContextBundle, ...]:
        return tuple(
            sorted(
                self._bundles.values(),
                key=lambda item: (item.created_at, item.context_bundle_id),
            )
        )


class JsonContextBundleRepository(InMemoryContextBundleRepository):
    """Small local/reference repository using one atomically replaced JSON document."""

    SCHEMA = "context-bundle-repository/v1"

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        if self.path.exists():
            self._restore()

    def put(self, bundle: ContextBundle) -> ContextBundle:
        existing = self.get_by_digest(bundle.digest)
        if existing is not None:
            return existing
        stored = super().put(bundle)
        self._save()
        return stored

    def _save(self) -> None:
        document: dict[str, JsonValue] = {
            "schema": self.SCHEMA,
            "bundles": [item.to_json(include_inline_content=True) for item in self.list_all()],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(f".{self.path.name}.tmp")
        temporary.write_text(
            json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def _restore(self) -> None:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        document = _object(raw, "context bundle repository")
        if _string(document, "schema") != self.SCHEMA:
            raise ValueError("unsupported context bundle repository schema")
        bundles = document.get("bundles")
        if not isinstance(bundles, list):
            raise ValueError("context bundle repository bundles must be an array")
        for raw_bundle in bundles:
            bundle = context_bundle_from_json(raw_bundle)
            super().put(bundle)


def context_bundle_from_json(value: object) -> ContextBundle:
    data = _object(value, "ContextBundle")
    if _string(data, "schema") != "context-bundle/v1":
        raise ValueError("unsupported ContextBundle schema")
    expected_digest = _string(data, "digest")
    entries_raw = _array(data, "entries")
    omissions_raw = _array(data, "omissions")
    bundle = ContextBundle(
        context_bundle_id=_string(data, "context_bundle_id"),
        task_id=_string(data, "task_id"),
        run_id=_string(data, "run_id"),
        agent_id=_string(data, "agent_id"),
        agent_revision=_int(data, "agent_revision"),
        entries=tuple(_entry(item) for item in entries_raw),
        omissions=tuple(_omission(item) for item in omissions_raw),
        budget=_budget(data.get("budget")),
        usage=_usage(data.get("usage")),
        resolver_version=_string(data, "resolver_version"),
        policy_version=_string(data, "policy_version"),
        actor_ref=_string(data, "actor_ref"),
        plan_id=_optional_string(data, "plan_id"),
        step_id=_optional_string(data, "step_id"),
        skill_bundle_id=_optional_string(data, "skill_bundle_id"),
        skill_bundle_digest=_optional_string(data, "skill_bundle_digest"),
        created_at=_datetime(data, "created_at"),
        reproducibility_limited=_bool(data, "reproducibility_limited"),
    )
    if bundle.digest != expected_digest:
        raise ValueError("stored ContextBundle digest does not match canonical payload")
    return bundle


def _source(value: object) -> ContextSourceRef:
    data = _object(value, "ContextSourceRef")
    return ContextSourceRef(
        source_type=ContextSourceType(_string(data, "source_type")),
        source_id=_string(data, "source_id"),
        revision=_optional_string(data, "revision"),
        digest=_optional_string(data, "digest"),
        snapshot_id=_optional_string(data, "snapshot_id"),
        locator=_optional_string(data, "locator"),
    )


def _transformation(value: object) -> ContextTransformation | None:
    if value is None:
        return None
    data = _object(value, "ContextTransformation")
    return ContextTransformation(
        kind=ContextTransformationKind(_string(data, "kind")),
        policy_version=_string(data, "policy_version"),
        input_digest=_string(data, "input_digest"),
        output_digest=_string(data, "output_digest"),
        details=_json_mapping(data.get("details"), "ContextTransformation.details"),
    )


def _entry(value: object) -> ContextEntry:
    data = _object(value, "ContextEntry")
    return ContextEntry(
        ordinal=_int(data, "ordinal"),
        source=_source(data.get("source")),
        role=ContextEntryRole(_string(data, "role")),
        selection_reason=_string(data, "selection_reason"),
        mandatory=_bool(data, "mandatory"),
        content_digest=_string(data, "content_digest"),
        inline_content=_optional_string_allow_empty(data, "inline_content"),
        content_ref=_optional_string(data, "content_ref"),
        freshness=ContextFreshness(_string(data, "freshness")),
        trust=ContextTrust(_string(data, "trust")),
        data_classification=ContextDataClassification(_string(data, "data_classification")),
        priority=_int(data, "priority"),
        relevance=_float(data, "relevance"),
        estimated_tokens=_int(data, "estimated_tokens"),
        content_bytes=_int(data, "content_bytes"),
        project_id=_optional_string(data, "project_id"),
        workspace_id=_optional_string(data, "workspace_id"),
        conflict_key=_optional_string(data, "conflict_key"),
        security_labels=_string_tuple(data.get("security_labels"), "security_labels"),
        transformation=_transformation(data.get("transformation")),
        metadata=_json_mapping(data.get("metadata"), "ContextEntry.metadata"),
    )


def _omission(value: object) -> ContextOmission:
    data = _object(value, "ContextOmission")
    return ContextOmission(
        source=_source(data.get("source")),
        reason=ContextOmissionReason(_string(data, "reason")),
        mandatory=_bool(data, "mandatory"),
        detail=_string(data, "detail"),
        content_digest=_optional_string(data, "content_digest"),
    )


def _budget(value: object) -> ContextBudget:
    data = _object(value, "ContextBudget")
    return ContextBudget(
        max_tokens=_optional_int(data, "max_tokens"),
        max_bytes=_optional_int(data, "max_bytes"),
        max_items=_optional_int(data, "max_items"),
    )


def _usage(value: object) -> ContextBudgetUsage:
    data = _object(value, "ContextBudgetUsage")
    return ContextBudgetUsage(
        estimated_tokens=_int(data, "estimated_tokens"),
        bytes=_int(data, "bytes"),
        items=_int(data, "items"),
        estimator_id=_string(data, "estimator_id"),
    )


def _object(value: object, name: str) -> dict[str, JsonValue]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a JSON object")
    if not all(isinstance(key, str) and _is_json_value(item) for key, item in value.items()):
        raise ValueError(f"{name} contains non-JSON values")
    return cast(dict[str, JsonValue], value)


def _is_json_value(value: object) -> bool:
    if value is None or isinstance(value, str | int | float | bool):
        return True
    if isinstance(value, list):
        return all(_is_json_value(item) for item in value)
    if isinstance(value, dict):
        return all(isinstance(key, str) and _is_json_value(item) for key, item in value.items())
    return False


def _array(data: dict[str, JsonValue], key: str) -> list[JsonValue]:
    value = data.get(key)
    if not isinstance(value, list):
        raise ValueError(f"{key} must be an array")
    return value


def _string(data: dict[str, JsonValue], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string")
    return value


def _optional_string(data: dict[str, JsonValue], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{key} must be a non-blank string or null")
    return value


def _optional_string_allow_empty(data: dict[str, JsonValue], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string or null")
    return value


def _int(data: dict[str, JsonValue], key: str) -> int:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer")
    return value


def _optional_int(data: dict[str, JsonValue], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer or null")
    return value


def _float(data: dict[str, JsonValue], key: str) -> float:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{key} must be numeric")
    return float(value)


def _bool(data: dict[str, JsonValue], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be boolean")
    return value


def _string_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ValueError(f"{name} must be an array of non-blank strings")
    return tuple(value)


def _json_mapping(value: object, name: str) -> dict[str, JsonValue]:
    return _object(value, name)


def _datetime(data: dict[str, JsonValue], key: str) -> datetime:
    value = _string(data, key)
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{key} must be an ISO-8601 datetime") from exc
