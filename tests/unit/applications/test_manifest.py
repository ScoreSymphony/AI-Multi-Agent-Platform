from __future__ import annotations

import pytest
from jsonschema import ValidationError

from ai_multi_agent_platform.applications.manifest import application_manifest_from_document
from ai_multi_agent_platform.applications.models import (
    APPLICATION_MANIFEST_SCHEMA_VERSION,
    ApplicationEndpointExposure,
    ApplicationServiceRuntime,
)
from ai_multi_agent_platform.domain import new_id


def _document() -> dict[str, object]:
    return {
        "schema_version": APPLICATION_MANIFEST_SCHEMA_VERSION,
        "application_id": new_id("application"),
        "name": "Notebook stack",
        "version": "1.0.0",
        "description": "Managed notebook application",
        "services": [
            {
                "service_id": "web",
                "runtime": "oci_image",
                "image": "example/notebook:1.0",
                "endpoints": [
                    {
                        "name": "web",
                        "target_port": 8888,
                        "exposure": "user",
                    }
                ],
                "health_check": {
                    "kind": "endpoint",
                    "endpoint_name": "web",
                },
            }
        ],
        "configuration": [
            {
                "name": "theme",
                "value_type": "string",
                "default": "light",
                "environment_variable": "APP_THEME",
            }
        ],
        "secrets": [
            {
                "name": "token",
                "environment_variable": "APP_TOKEN",
            }
        ],
        "resources": {
            "cpu_cores": 2,
            "memory_bytes": 4_294_967_296,
            "architectures": ["amd64"],
        },
        "ui": {
            "endpoint_ref": "web.web",
            "open_mode": "embedded",
        },
    }


def test_manifest_document_constructs_provider_neutral_canonical_model() -> None:
    manifest = application_manifest_from_document(_document())

    assert manifest.services[0].runtime is ApplicationServiceRuntime.OCI_IMAGE
    assert manifest.services[0].image == "example/notebook:1.0"
    assert manifest.services[0].endpoints[0].exposure is ApplicationEndpointExposure.USER
    assert manifest.ui is not None
    assert manifest.ui.endpoint_ref == "web.web"
    assert manifest.resources.cpu_cores == 2


def test_manifest_schema_rejects_unknown_provider_specific_fields() -> None:
    document = _document()
    services = document["services"]
    assert isinstance(services, list)
    service = services[0]
    assert isinstance(service, dict)
    service["docker_container_id"] = "private-runtime-id"

    with pytest.raises(ValidationError, match="Additional properties are not allowed"):
        application_manifest_from_document(document)


def test_manifest_semantics_reject_unknown_dependency() -> None:
    document = _document()
    services = document["services"]
    assert isinstance(services, list)
    service = services[0]
    assert isinstance(service, dict)
    service["depends_on"] = ["missing"]

    with pytest.raises(ValueError, match="depends on unknown services"):
        application_manifest_from_document(document)
