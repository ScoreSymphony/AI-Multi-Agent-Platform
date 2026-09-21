"""Shared OpenAPI error-contract normalization."""

from __future__ import annotations

from typing import Any


_HTTP_METHODS = ("get", "put", "post", "delete", "options", "head", "patch", "trace")


def ensure_method_not_allowed_responses(specification: dict[str, Any]) -> dict[str, Any]:
    """Add the canonical 405 response to every declared public OpenAPI operation."""

    paths = specification.get("paths")
    if not isinstance(paths, dict):
        return specification

    for path_item in paths.values():
        if not isinstance(path_item, dict):
            continue
        for method in _HTTP_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            responses = operation.get("responses")
            if isinstance(responses, dict):
                responses.setdefault(
                    "405",
                    {"$ref": "#/components/responses/Error"},
                )
    return specification
