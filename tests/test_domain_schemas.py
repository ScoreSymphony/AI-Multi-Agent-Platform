import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator, ValidationError
from referencing import Registry, Resource

SCHEMA_DIR = Path(__file__).parents[1] / "schemas" / "domain"
SCHEMA_NAMES = ("common", "task", "run", "event")


def _load_schemas() -> dict[str, dict[str, Any]]:
    return {
        name: json.loads((SCHEMA_DIR / f"{name}.schema.json").read_text(encoding="utf-8"))
        for name in SCHEMA_NAMES
    }


def _registry(schemas: dict[str, dict[str, Any]]) -> Registry:
    return Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
    )


def _validator(name: str) -> Draft202012Validator:
    schemas = _load_schemas()
    return Draft202012Validator(schemas[name], registry=_registry(schemas))


def _task(**overrides: Any) -> dict[str, Any]:
    task: dict[str, Any] = {
        "id": "task_123e4567-e89b-12d3-a456-426614174000",
        "schema_version": "1.0",
        "title": "Reference task",
        "status": "ready",
        "owner_ref": {"type": "user", "id": "user-1"},
        "created_at": "2026-09-02T16:00:00Z",
        "updated_at": "2026-09-02T16:00:00Z",
        "correlation_id": "corr-1",
        "labels": ["reference"],
        "metadata": {},
        "external_refs": [],
    }
    task.update(overrides)
    return task


def _run(**overrides: Any) -> dict[str, Any]:
    run: dict[str, Any] = {
        "id": "run_123e4567-e89b-12d3-a456-426614174001",
        "schema_version": "1.0",
        "subject_type": "task",
        "subject_id": "task_123e4567-e89b-12d3-a456-426614174000",
        "attempt": 1,
        "status": "queued",
        "owner_ref": {"type": "service", "id": "platform"},
        "created_at": "2026-09-02T16:00:00Z",
        "updated_at": "2026-09-02T16:00:00Z",
        "correlation_id": "corr-1",
        "external_refs": [],
        "metadata": {},
    }
    run.update(overrides)
    return run


def test_domain_schemas_are_valid_draft_2020_12() -> None:
    for schema in _load_schemas().values():
        Draft202012Validator.check_schema(schema)


def test_canonical_task_example_validates() -> None:
    _validator("task").validate(_task())


def test_waiting_task_example_validates() -> None:
    _validator("task").validate(_task(status="waiting"))


def test_canonical_run_example_validates() -> None:
    _validator("run").validate(_run(trace_id="trace-1"))


def test_canonical_step_run_example_validates() -> None:
    _validator("run").validate(
        _run(
            subject_type="step",
            subject_id="step_123e4567-e89b-12d3-a456-426614174003",
        )
    )


def test_canonical_event_example_validates() -> None:
    event = {
        "id": "event_123e4567-e89b-12d3-a456-426614174002",
        "schema_version": "1.0",
        "event_type": "task.ready",
        "occurred_at": "2026-09-02T16:00:00Z",
        "subject_type": "task",
        "subject_id": "task_123e4567-e89b-12d3-a456-426614174000",
        "correlation_id": "corr-1",
        "causation_id": None,
        "trace_id": "trace-1",
        "payload": {},
        "external_refs": [],
    }
    _validator("event").validate(event)


def test_task_rejects_backend_specific_status() -> None:
    with pytest.raises(ValidationError):
        _validator("task").validate(_task(status="forge_executing"))


def test_task_rejects_malformed_uuid_payload() -> None:
    with pytest.raises(ValidationError):
        _validator("task").validate(_task(id="task_------------------------------------"))


def test_run_rejects_backend_specific_subject_id() -> None:
    with pytest.raises(ValidationError):
        _validator("run").validate(_run(subject_id="forge-job-123"))


def test_run_rejects_step_id_when_subject_type_is_task() -> None:
    with pytest.raises(ValidationError):
        _validator("run").validate(
            _run(subject_id="step_123e4567-e89b-12d3-a456-426614174003")
        )
