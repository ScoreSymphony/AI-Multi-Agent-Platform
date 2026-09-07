"""Stable human and machine-readable CLI rendering."""

from __future__ import annotations

import json
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TextIO

from ai_multi_agent_platform.contracts.types import JsonValue
from ai_multi_agent_platform.security.redaction import redact_sensitive

from .client import APIClientError, ClientResponse, TransportError
from .profiles import ProfileError


@dataclass(frozen=True, slots=True)
class LocalCLIError:
    code: str
    category: str
    message: str
    retryable: bool = False


class Renderer:
    def __init__(
        self,
        *,
        json_mode: bool,
        verbose: bool,
        stdout: TextIO | None = None,
        stderr: TextIO | None = None,
    ) -> None:
        self.json_mode = json_mode
        self.verbose = verbose
        self.stdout = stdout or sys.stdout
        self.stderr = stderr or sys.stderr

    def success(self, response: ClientResponse) -> None:
        safe_body = _redacted(response.body)
        if self.json_mode:
            payload = {
                "data": safe_body,
                "meta": {
                    "status": response.status,
                    "request_id": response.request_id,
                    "correlation_id": response.correlation_id,
                    "api_version": response.api_version,
                },
            }
            self.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
            return
        self._human(safe_body)
        if self.verbose:
            if response.request_id:
                self.stdout.write(f"Request ID: {response.request_id}\n")
            if response.correlation_id:
                self.stdout.write(f"Correlation ID: {response.correlation_id}\n")

    def local_success(self, data: JsonValue) -> None:
        safe_data = _redacted(data)
        if self.json_mode:
            payload = {"data": safe_data, "meta": {"local": True}}
            self.stdout.write(json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n")
        else:
            self._human(safe_data)

    def error(self, error: APIClientError | TransportError | ProfileError | LocalCLIError) -> None:
        normalized = _redacted(_normalize_error(error))
        if not isinstance(normalized, dict):
            raise TypeError("redacted CLI error must remain a JSON object")
        if self.json_mode:
            self.stderr.write(json.dumps(normalized, sort_keys=True, separators=(",", ":")) + "\n")
            return
        self.stderr.write(f"Error [{normalized['code']}]: {normalized['message']}\n")
        request_id = normalized.get("request_id")
        correlation_id = normalized.get("correlation_id")
        if request_id:
            self.stderr.write(f"Request ID: {request_id}\n")
        if correlation_id:
            self.stderr.write(f"Correlation ID: {correlation_id}\n")

    def _human(self, value: JsonValue) -> None:
        if isinstance(value, dict):
            items = value.get("items")
            if isinstance(items, list):
                self._table(items)
                total = value.get("total")
                next_cursor = value.get("next_cursor")
                if isinstance(total, int):
                    self.stdout.write(f"Total: {total}\n")
                if isinstance(next_cursor, str):
                    self.stdout.write(f"Next cursor: {next_cursor}\n")
                return
            for key, item in value.items():
                self.stdout.write(f"{key}: {_display(item)}\n")
            return
        if isinstance(value, list):
            self._table(value)
            return
        self.stdout.write(f"{_display(value)}\n")

    def _table(self, items: list[JsonValue]) -> None:
        if not items:
            self.stdout.write("No items.\n")
            return
        rows = [item for item in items if isinstance(item, dict)]
        if len(rows) != len(items):
            for item in items:
                self.stdout.write(f"{_display(item)}\n")
            return
        preferred = [
            "id",
            "name",
            "title",
            "status",
            "attempt",
            "coordination_phase",
            "current_attempt",
            "dependency_ids",
            "satisfied_dependency_ids",
            "latest_run_id",
            "wait_type",
            "wait_deadline_at",
            "retry_due_at",
            "reconciliation",
            "project_id",
            "task_id",
            "provider_id",
            "enabled",
            "health",
        ]
        columns = [column for column in preferred if any(column in row for row in rows)]
        if not columns:
            columns = list(rows[0].keys())[:6]
        widths = {
            column: max(len(column), *(len(_display(row.get(column))) for row in rows))
            for column in columns
        }
        header = "  ".join(column.ljust(widths[column]) for column in columns)
        self.stdout.write(header + "\n")
        self.stdout.write("  ".join("-" * widths[column] for column in columns) + "\n")
        for row in rows:
            rendered = "  ".join(
                _display(row.get(column)).ljust(widths[column]) for column in columns
            )
            self.stdout.write(rendered + "\n")


def _normalize_error(
    error: APIClientError | TransportError | ProfileError | LocalCLIError,
) -> dict[str, JsonValue]:
    if isinstance(error, APIClientError):
        return {
            "code": error.code,
            "category": error.category,
            "message": error.message,
            "status": error.status,
            "retryable": error.retryable,
            "request_id": error.request_id,
            "correlation_id": error.correlation_id,
            "details": error.details,
        }
    if isinstance(error, LocalCLIError):
        return {
            "code": error.code,
            "category": error.category,
            "message": error.message,
            "retryable": error.retryable,
        }
    if isinstance(error, ProfileError):
        return {
            "code": "invalid_cli_configuration",
            "category": "configuration",
            "message": str(error),
            "retryable": False,
        }
    return {
        "code": "control_plane_unreachable",
        "category": "transport",
        "message": str(error),
        "retryable": True,
    }


def _redacted(value: JsonValue) -> JsonValue:
    return redact_sensitive(value)


def _display(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Mapping | list | tuple):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return str(value)
