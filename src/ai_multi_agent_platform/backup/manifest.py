"""Runtime loading and validation for the versioned backup manifest schema."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib.resources import files
from typing import Any, cast

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import SchemaError, ValidationError

_SCHEMA_RESOURCE = "backup-manifest-v1.schema.json"


class ManifestSchemaError(ValueError):
    """Raised when a backup manifest does not conform to the packaged schema."""


@lru_cache(maxsize=1)
def backup_manifest_v1_schema() -> dict[str, Any]:
    """Load the packaged schema used by installed backup verification code."""

    raw = (
        files("ai_multi_agent_platform.backup")
        .joinpath(_SCHEMA_RESOURCE)
        .read_text(encoding="utf-8")
    )
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ManifestSchemaError("packaged backup manifest schema must be a JSON object")
    schema = cast(dict[str, Any], payload)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:  # pragma: no cover - repository/schema parity tests guard this
        raise ManifestSchemaError(
            f"packaged backup manifest schema is invalid: {exc.message}"
        ) from exc
    return schema


def validate_backup_manifest_v1(value: Any) -> dict[str, Any]:
    """Validate one manifest with structural and date-time format checks enabled."""

    schema = backup_manifest_v1_schema()
    try:
        Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)
    except ValidationError as exc:
        location = ".".join(str(part) for part in exc.absolute_path)
        suffix = f" at {location}" if location else ""
        raise ManifestSchemaError(f"{exc.message}{suffix}") from exc
    if not isinstance(value, dict):  # narrowed by the schema, retained for static typing
        raise ManifestSchemaError("backup manifest must be a JSON object")
    return cast(dict[str, Any], value)
