"""Capability declarations for the stdlib browser reference provider."""

from __future__ import annotations

from collections.abc import Mapping

from ai_multi_agent_platform.capabilities.types import (
    CapabilityRegistration,
    CapabilitySpec,
    SafetyClassification,
    SideEffectClassification,
)
from ai_multi_agent_platform.contracts.types import HealthStatus, JsonValue

BROWSER_NAVIGATE_CAPABILITY_ID = "browser.navigate"
BROWSER_EXTRACT_CAPABILITY_ID = "browser.extract"
BROWSER_FOLLOW_LINK_CAPABILITY_ID = "browser.follow_link"
BROWSER_SUBMIT_FORM_CAPABILITY_ID = "browser.submit_form"
BROWSER_DOWNLOAD_CAPABILITY_ID = "browser.download"
BROWSER_CLOSE_SESSION_CAPABILITY_ID = "browser.close_session"

NAVIGATE_TOOL_REF = "browser.reference.navigate"
EXTRACT_TOOL_REF = "browser.reference.extract"
FOLLOW_LINK_TOOL_REF = "browser.reference.follow_link"
SUBMIT_FORM_TOOL_REF = "browser.reference.submit_form"
DOWNLOAD_TOOL_REF = "browser.reference.download"
CLOSE_SESSION_TOOL_REF = "browser.reference.close_session"

CONTENT_TRUST = "untrusted_web_content"


def browser_capability_registrations(provider_id: str) -> tuple[CapabilityRegistration, ...]:
    """Build the canonical registrations exposed by the stdlib reference provider."""

    common_output = _page_output_schema()
    specs = (
        (_navigate_spec(common_output), NAVIGATE_TOOL_REF),
        (_extract_spec(), EXTRACT_TOOL_REF),
        (_follow_link_spec(common_output), FOLLOW_LINK_TOOL_REF),
        (_submit_form_spec(common_output), SUBMIT_FORM_TOOL_REF),
        (_download_spec(), DOWNLOAD_TOOL_REF),
        (_close_session_spec(), CLOSE_SESSION_TOOL_REF),
    )
    return tuple(
        CapabilityRegistration(
            capability=spec,
            provider_id=provider_id,
            provider_tool_ref=tool_ref,
            priority=100,
        )
        for spec, tool_ref in specs
    )


def _navigate_spec(common_output: dict[str, JsonValue]) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=BROWSER_NAVIGATE_CAPABILITY_ID,
        name="Navigate browser",
        description="Open a HTTP(S) URL in an isolated canonical browser session.",
        version="1.0",
        input_schema=_object_schema(
            {
                "url": {"type": "string", "minLength": 1},
                "session_id": {"type": "string", "minLength": 1},
            },
            required=("url",),
        ),
        output_schema=common_output,
        tags=("browser", "web", "read"),
        side_effects=SideEffectClassification.NONE,
        required_permissions=("browser.network.read",),
        health=HealthStatus.HEALTHY,
        features=("http", "https", "cookies", "session_reuse"),
    )


def _extract_spec() -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=BROWSER_EXTRACT_CAPABILITY_ID,
        name="Extract browser page",
        description="Extract untrusted text/link metadata from the current page.",
        version="1.0",
        input_schema=_object_schema(
            {
                "session_id": {"type": "string", "minLength": 1},
                "find": {"type": "string"},
                "include_html": {"type": "boolean"},
            },
            required=("session_id",),
        ),
        output_schema={"type": "object"},
        tags=("browser", "web", "extract", "untrusted-content"),
        side_effects=SideEffectClassification.NONE,
        required_permissions=("browser.content.read",),
        health=HealthStatus.HEALTHY,
        features=("text", "links", "find", "html"),
    )


def _follow_link_spec(common_output: dict[str, JsonValue]) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=BROWSER_FOLLOW_LINK_CAPABILITY_ID,
        name="Follow browser link",
        description="Follow one href or visible link text in the current page.",
        version="1.0",
        input_schema={
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "minLength": 1},
                "href": {"type": "string", "minLength": 1},
                "link_text": {"type": "string", "minLength": 1},
            },
            "required": ["session_id"],
            "oneOf": [{"required": ["href"]}, {"required": ["link_text"]}],
            "additionalProperties": False,
        },
        output_schema=common_output,
        tags=("browser", "web", "read"),
        side_effects=SideEffectClassification.NONE,
        required_permissions=("browser.network.read",),
        health=HealthStatus.HEALTHY,
    )


def _submit_form_spec(common_output: dict[str, JsonValue]) -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=BROWSER_SUBMIT_FORM_CAPABILITY_ID,
        name="Submit browser form",
        description="Submit a HTML form, optionally uploading one authorized canonical file.",
        version="1.0",
        input_schema=_object_schema(
            {
                "session_id": {"type": "string", "minLength": 1},
                "form_index": {"type": "integer", "minimum": 0},
                "fields": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                },
                "file_upload": {
                    "type": "object",
                    "properties": {
                        "field": {"type": "string", "minLength": 1},
                        "file_ref": {"type": "string", "minLength": 1},
                        "filename": {"type": "string", "minLength": 1},
                        "content_type": {"type": "string", "minLength": 1},
                    },
                    "required": ["field", "file_ref", "filename"],
                    "additionalProperties": False,
                },
            },
            required=("session_id",),
        ),
        output_schema=common_output,
        tags=("browser", "web", "external-side-effect", "upload"),
        safety=SafetyClassification.RESTRICTED,
        side_effects=SideEffectClassification.EXTERNAL,
        required_permissions=("browser.external.submit", "file.read"),
        health=HealthStatus.HEALTHY,
        features=("forms", "canonical_file_upload"),
    )


def _download_spec() -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=BROWSER_DOWNLOAD_CAPABILITY_ID,
        name="Download browser file",
        description=(
            "Download HTTP(S) content into canonical FileProvider/Artifact storage with provenance."
        ),
        version="1.0",
        input_schema=_object_schema(
            {
                "url": {"type": "string", "minLength": 1},
                "session_id": {"type": "string", "minLength": 1},
            },
            required=("url",),
        ),
        output_schema={"type": "object"},
        tags=("browser", "web", "download", "file", "artifact"),
        safety=SafetyClassification.RESTRICTED,
        side_effects=SideEffectClassification.LOCAL_WRITE,
        required_permissions=("browser.network.read", "file.create", "artifact.create"),
        health=HealthStatus.HEALTHY,
        features=(
            "canonical_file_download",
            "canonical_artifact_link",
            "sha256",
            "provenance",
        ),
    )


def _close_session_spec() -> CapabilitySpec:
    return CapabilitySpec(
        capability_id=BROWSER_CLOSE_SESSION_CAPABILITY_ID,
        name="Close browser session",
        description="Close an isolated canonical browser session.",
        version="1.0",
        input_schema=_object_schema(
            {"session_id": {"type": "string", "minLength": 1}},
            required=("session_id",),
        ),
        output_schema={
            "type": "object",
            "properties": {
                "session_id": {"type": "string"},
                "closed": {"const": True},
            },
            "required": ["session_id", "closed"],
            "additionalProperties": False,
        },
        tags=("browser", "session"),
        side_effects=SideEffectClassification.NONE,
        required_permissions=("browser.session.manage",),
        health=HealthStatus.HEALTHY,
    )


def _page_output_schema() -> dict[str, JsonValue]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "url": {"type": "string"},
            "status_code": {"type": "integer"},
            "title": {"type": "string"},
            "content_type": {"type": ["string", "null"]},
            "size_bytes": {"type": "integer", "minimum": 0},
            "content_trust": {"const": CONTENT_TRUST},
        },
        "required": [
            "session_id",
            "url",
            "status_code",
            "title",
            "content_type",
            "size_bytes",
            "content_trust",
        ],
        "additionalProperties": False,
    }


def _object_schema(
    properties: Mapping[str, JsonValue],
    *,
    required: tuple[str, ...] = (),
) -> dict[str, JsonValue]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "type": "object",
        "properties": dict(properties),
        "required": list(required),
        "additionalProperties": False,
    }
